#!/usr/bin/env python3
import argparse
import json
import re
import requests
from collections import defaultdict


def load_data(fn):
    with open(fn, 'r') as f:
        return json.load(f)


class GitlabClient:
    def __init__(self, base_url, auth_token):
        # normalize
        self.base_url = base_url.strip('/')
        self.auth_token = auth_token

    def _request(self, method, address, **kwargs):
        url = '{}/{}'.format(self.base_url, address)
        headers = kwargs.get('headers', {})
        headers['PRIVATE-TOKEN'] = self.auth_token
        kwargs['headers'] = headers
        response = method(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def delete(self, address, **kwargs):
        return self._request(requests.delete, address, **kwargs)

    def get(self, address, **kwargs):
        # FIXME handle pagination???
        return self._request(requests.get, address, **kwargs)

    def post(self, address, **kwargs):
        return self._request(requests.post, address, **kwargs)

    def put(self, address, **kwargs):
        return self._request(requests.put, address, **kwargs)

    @classmethod
    def split_project_url(cls, url):
        parts = re.match(r'^(https?://.+)/([\w_-]+)/([\w_-]+)$', url)
        if parts is None:
            raise ValueError("Invalid gitlab project url {}".format(url))
        return {
            'base_url': parts.group(1),
            'project_id': '{}%2f{}'.format(parts.group(2), parts.group(3))
        }

    @classmethod
    def project_to_base_url(cls, url):
        return "{}/api/v3".format(
            cls.split_project_url(url)['base_url'])

    @classmethod
    def project_to_api_url(cls, url):
        parts = cls.split_project_url(url)
        return "{}/api/v3/projects/{}".format(parts['base_url'],
            parts['project_id'])


def create_milestone(client, milestone):
    result = client.post(
        'milestones',
        data={
            'title': milestone['title'],
            'description': milestone['description'],
            'due_date': milestone['due_date'],
            'start_date': milestone['start_date']
        }
    )
    if milestone['is_closed']:
        client.put(
            'milestones/{}'.format(result['id']),
            data={
                'state_event': 'close'
            }
        )


def create_milestones(client, milestones):
    for (mid, milestone) in milestones.items():
        print('Creating milestone {}'.format(mid))
        create_milestone(client, milestone)


def get_milestone_map(client, milestones):
    result = client.get('milestones')
    gitlab_milestones = {m['title']: m['id'] for m in result}
    milestone_map = {int(mid): gitlab_milestones[m['title']]
        for (mid, m) in milestones.items()}
    return milestone_map


def create_issue(client, issue, milestone_map, user_map):
    # FIXME convert issue description
    # labels are automatically created on demand
    result = client.post(
        'issues',
        data={
            'title': issue['title'],
            'description': issue['description'],
            'assignee_id': user_map.get(issue['assignee_id']),
            'milestone_id': milestone_map.get(issue['milestone_id']),
            'labels': ','.join(issue['labels']),
            'created_at': issue['created_at'],
            'due_date': issue['due_date']
        },
        headers={'SUDO': user_map[issue['author_id']]}
    )
    # FIXME preserve start date

    for action in issue['actions']:
        is_only_note = set(action.keys()) == set(
            ('author_id', 'created_at', 'notes'))
        if not is_only_note:
            data = {
                'title': action.get('title'),
                'description': action.get('description'),
                'assignee_id': user_map.get(
                    action.get('assignee_id')),
                'milestone_id': milestone_map.get(
                    action.get('milestone_id')),
                'updated_at': action['created_at'],
                'due_date': action.get('due_date')
            }
            if 'labels' in action:
                data['labels'] = ','.join(action['labels'])
            if 'is_closed' in action:
                data['state_event'] = 'close' if action['is_closed'] \
                    else 'reopen'

            client.put(
                'issues/{}'.format(result['id']),
                data=data,
                headers={'SUDO': user_map[action['author_id']]}
            )
        if 'notes' in action:
            client.post(
                'issues/{}/notes'.format(result['id']),
                data={
                    'body': action['notes'],
                    'created_at': action['created_at']
                },
                headers={'SUDO': user_map[action['author_id']]}
            )

    for watcher in issue['watcher_ids']:
        if watcher not in user_map:
            continue
        try:
            # FIXME what is the actual problem here?
            client.post(
                'issues/{}/subscription'.format(result['id']),
                headers={'SUDO': user_map[watcher]}
            )
        except ValueError:
            pass

    # TODO handle hierarchy?
    return (result['id'], result['iid'])


def add_relations(client, issue, issue_id):
    relations_text = []
    for (name, to_id) in issue['relations']:
        text = '{} #{}'.format(name.replace('s_inv', 'ed by'), to_id)
        relations_text.append(text)
    relations_text = '\n'.join(relations_text)
    if len(relations_text) > 0:
        issue_url = 'issues/{}'.format(issue_id)
        result = client.get(issue_url)
        result['description'] += '\n\n###### Relations\n' + relations_text
        client.put(issue_url, data=result)


def pad_issue_id(next_id, last_id):
    while last_id + 1 < next_id:
        result = client.post('issues', data={'title': 'TMP'})
        client.delete('issues/{}'.format(result['id']))
        if result['iid'] >= next_id:
            raise ValueError("Project seems to already have issues")
        if result['iid'] <= last_id:
            raise AssertionError("Bad internal state {} {}".format(
                result['iid'], last_id))
        last_id = result['iid']


def create_issues(client, issues, milestone_map, user_map):
    # iterate issues in order!
    last_id = 0
    id_map = {}
    for iid in sorted([int(i) for i in issues.keys()]):
        issue = issues[str(iid)]
        print('Creating issue {}'.format(iid))
        pad_issue_id(iid, last_id)
        (gitlab_id, gitlab_iid) = create_issue(client, issue,
            milestone_map, user_map)
        assert gitlab_iid == iid, "ID mismatch"
        id_map[iid] = gitlab_id
        last_id = iid

    for (iid, gitlab_id) in id_map.items():
        add_relations(client, issues[str(iid)], gitlab_id)


def convert_board(client, board, user_map):
    # boards have no attached milestone
    milestone_map = {}
    # iterate issues in order
    for iid in sorted([int(i) for i in board.keys()]):
        issue = board[str(iid)]
        print('Creating board issue {}'.format(iid))
        create_issue(client, issue, milestone_map, user_map)


def convert_boards(client, boards, user_map):
    for board in boards.values():
        convert_board(client, board['issues'], user_map)


def get_issue_users(issues, users):
    for issue in issues.values():
        users.add(issue['author_id'])
        users.add(issue['assignee_id'])
        users.update(issue['watcher_ids'])
        for action in issue['actions']:
            users.add(issue['author_id'])
            users.add(issue.get('assignee_id'))


def get_active_users(issues, boards):
    users = set()
    get_issue_users(issues, users)
    for board in boards.values():
        get_issue_users(board['issues'], users)
    users.discard(None)
    return users


def get_users(system_client):
    result = system_client.get('users')
    user_map = {}
    for user in result:
        user_map[user['username']] = user['id']
    return user_map


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('project_url')
    parser.add_argument('auth_token')
    parser.add_argument('source_file')
    args = parser.parse_args()

    data = load_data(args.source_file)
    client = GitlabClient(
        GitlabClient.project_to_api_url(args.project_url),
        args.auth_token
    )

    # # create milestones
    create_milestones(client, data['milestones'])
    milestone_map = get_milestone_map(client, data['milestones'])

    active_users = get_active_users(data['issues'], data['boards'])
    system_client = GitlabClient(
        GitlabClient.project_to_base_url(args.project_url),
        args.auth_token
    )
    user_map = get_users(system_client)
    unknown_users = active_users - set(user_map.keys())
    if len(unknown_users) > 0:
        for user in sorted(unknown_users):
            print('Unknown user {}'.format(user))

    DEFAULT_USER_ID = 1
    spare_user_map = defaultdict(lambda: DEFAULT_USER_ID, user_map)
    create_issues(client, data['issues'], milestone_map,
        spare_user_map)
    convert_boards(client, data['boards'], spare_user_map)