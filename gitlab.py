#!/usr/bin/env python3
import argparse
import json
import os.path
import re
import requests
import subprocess
from collections import defaultdict


def load_data(fn):
    with open(fn, 'r') as f:
        return json.load(f)


COMMIT_RE = re.compile(r'commit:([0-9a-f]{8,40})([^\s]?)')


def normalize_commit_id(match):
    cid = match.group(1)
    if len(cid) < 40:
        cid = cid[:8]
    next_char = match.group(2)
    if not next_char == "":
        # add whitespace after commit id
        next_char = ' ' + next_char
    return cid + next_char


def fix_commit_links(text):
    return COMMIT_RE.sub(normalize_commit_id, text)


WIKI_LINK_RE = re.compile(r'\\\[\\\[([^\]\|]+)(\|[^\]\|]+)?\\\]\\\]')


def normalize_wiki_link(match):
    slug = match.group(1).replace('\\#', '#')
    name = match.group(2)
    if name is None:
        name = slug.split('#')[0]
    else:
        name = name[1:]
    return '[' + name + '](' + slug + ')'


def fix_wiki_links(text):
    return WIKI_LINK_RE.sub(normalize_wiki_link, text)


CODE_BLOCK_RE = re.compile(r'<code class="([a-z]+)">')


def fix_code_blocks(text):
    # convert <code class="lua"> ... </code>
    # to ```lua \n ... \n ```
    lines = text.split('\n')
    output = []
    inside_code = False

    for line in lines:
        if not inside_code:
            match = CODE_BLOCK_RE.match(line.strip())
            if match is not None:
                lang = match.group(1)
                output.append('```' + lang)
                inside_code = True
            else:
                output.append(line)
        else:
            if line == '':
                output.append('')
                continue

            if len(line) < 4 or line[0:4].strip() != '':
                output.append(line)
                inside_code = False

            line = line[4:]
            if line.strip() == '</code>':
                inside_code = False
                line = '```'
            output.append(line)

    assert not inside_code
    return '\n'.join(output)


def convert_description(text):
    if text is None or len(text.strip()) == 0:
        return ''
    pandoc = subprocess.Popen(["pandoc", "-f", "textile",
        "-t", "markdown_github", "--atx-headers"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    (result, err) = pandoc.communicate(text.encode())
    assert pandoc.returncode == 0
    output = result.decode().strip()
    output = fix_commit_links(output)
    output = fix_code_blocks(output)
    return output


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
            'description': convert_description(milestone['description']),
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


def upload_file(client, file):
    FILE_BASE_PATH = 'file'
    fn = os.path.join(FILE_BASE_PATH, str(file['attachment_id']),
        file['file'])
    file_info = {'file': (file['file'], open(fn, 'rb'))}
    result = client.post('uploads', files=file_info)
    return result


def upload_attachments(client, attachments):
    results = []
    for attachment in attachments:
        print("Uploading attachment {}".format(attachment['file']))
        ref = upload_file(client, attachment)
        desc = attachment['description']
        if desc is None:
            desc = ''
        line = '{}: {}\n  {}'.format(attachment['file'],
            desc, ref['markdown'])
        results.append({
            'file': attachment['file'],
            'markdown': ref['markdown'],
            'description': line
        })
    return results


def make_attachement_str(uploaded_attachments):
    results = ['\n\n###### Attachments']
    for attachment in uploaded_attachments:
        results.append(attachment['description'])
    if len(results) > 1:
        return '\n- '.join(results)
    return ''


def create_attachments(client, attachments):
    uploads = upload_attachments(client, attachments)
    return make_attachement_str(uploads)


def create_issue(client, issue, milestone_map, user_map):
    attachment_str = create_attachments(client, issue['attachments'])
    start_date_str = ''
    if issue['start_date'] is not None:
        start_date_str = '\n\n###### Start date\n' + issue['start_date']

    # labels are automatically created on demand
    data = {
        'title': issue['title'],
        'description': convert_description(issue['description']),
        'assignee_id': user_map.get(issue['assignee_id']),
        'milestone_id': milestone_map.get(issue['milestone_id']),
        'labels': ','.join(issue['labels']),
        'created_at': issue['created_at'],
        'due_date': issue['due_date']
    }
    if data['description'] is None:
        data['description'] = ''
    data['description'] += start_date_str + attachment_str
    result = client.post(
        'issues',
        data=data,
        headers={'SUDO': user_map[issue['author_id']]}
    )

    last_description = issue['description']
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
            if data['description'] is not None:
                data['description'] = convert_description(data['description'])
                last_description = data['description']
            if 'start_date' in action:
                start_date_str = '\n\n###### Start date\n' + \
                    issue['start_date']
                data['description'] = last_description
            if data['description'] is not None:
                data['description'] += start_date_str + attachment_str

            client.put(
                'issues/{}'.format(result['id']),
                data=data,
                headers={'SUDO': user_map[action['author_id']]}
            )
        if 'notes' in action:
            note_suffix = ''
            if 'attachments' in action:
                note_suffix = create_attachments(client,
                    action['attachments'])
            client.post(
                'issues/{}/notes'.format(result['id']),
                data={
                    'body': convert_description(action['notes']) + note_suffix,
                    'created_at': action['created_at']
                },
                headers={'SUDO': user_map[action['author_id']]}
            )

    for watcher in issue['watcher_ids']:
        if watcher not in user_map:
            continue
        try:
            # just ignore errors here...
            client.post(
                'issues/{}/subscription'.format(result['id']),
                headers={'SUDO': user_map[watcher]}
            )
        except ValueError:
            pass

    return (result['id'], result['iid'])


def add_relations(client, issue, issue_id):
    relations_text = ['\n\n###### Relations']
    for (name, to_id) in issue['relations']:
        text = '{} #{}'.format(name.replace('es_inv', 'ed by')
            .replace('s_inv', 'ed by'), to_id)
        relations_text.append(text)
    if len(relations_text) > 1:
        issue_url = 'issues/{}'.format(issue_id)
        result = client.get(issue_url)
        if result['description'] is None:
            result['description'] = ''
        result = {
            'description': result['description'] + '\n- '.join(relations_text),
            'updated_at': result['updated_at']
        }
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


def get_issues(client):
    return client.get('issues')


def map_boards_to_milestones(boards):
    milestones = {}
    for (board_id, board) in boards.items():
        milestones[board_id] = {
            'title': "Board-" + board['name'],
            'description': None,
            'start_date': None,
            'due_date': None,
            'is_closed': False
        }
    return milestones


def convert_board(client, board, milestone_map, user_map):
    # iterate issues in order
    for iid in sorted([int(i) for i in board.keys()]):
        issue = board[str(iid)]
        print('Creating board issue {}'.format(iid))
        create_issue(client, issue, milestone_map, user_map)


def convert_boards(client, boards, milestone_map, user_map):
    for board in boards.values():
        convert_board(client, board['issues'], milestone_map, user_map)


class SimpleGitClient:
    def __init__(self, base_path):
        self.cwd = base_path
        os.makedirs(base_path)
        self._run('init', '-q')

    def _run(self, *params):
        subprocess.check_call(['git'] + list(params), cwd=self.cwd)

    @classmethod
    def format_user(cls, full_name, email):
        return '{} <{}>'.format(full_name, email)

    def commit_file(self, fn, text, user, date):
        with open(os.path.join(self.cwd, fn), 'wb') as f:
            f.write(text.encode())
        self._run('add', fn)
        self._run('commit', '-q', '-m', 'Wiki entry',
                '--author=' + user, '--date=' + date)

    def gc(self):
        self._run('gc', '--aggressive', '--quiet')


def wiki_attachments_map(uploaded_attachments):
    file_map = {}
    for attachment in uploaded_attachments:
        file_map[attachment['file']] = attachment['markdown']
    return file_map


EMBEDDED_ATTACHMENT_RE = re.compile(r'!\[\]\(([^\)]+)\)')
ATTACHMENT_LINK_RE = re.compile(r'attachment:([^\s]+)')


def insert_wiki_attachments(text, file_map):
    # handle ![](aufbau.png)
    def map_file(match):
        fn = match.group(1).replace('\\_', '_')
        if fn in file_map:
            return file_map[fn]
        else:
            return match.group(0)
    text = EMBEDDED_ATTACHMENT_RE.sub(map_file, text)
    text = ATTACHMENT_LINK_RE.sub(map_file, text)
    return text


def convert_wiki(wiki, project_name, users):
    user_map = {}
    for user in users.values():
        user_map[user['login']] = user

    repo = SimpleGitClient('{}.wiki.git'.format(project_name))
    for (wid, page) in wiki.items():
        # handle attachments
        uploads = upload_attachments(client, page['attachments'])
        file_map = wiki_attachments_map(uploads)
        attachment_str = make_attachement_str(uploads)

        fn = '{}.md'.format(page['slug'])
        title = page['title']
        # only add the title if it provides additional information
        title_slug = page['title'].lower().replace(' ', '-')
        if page['slug'] == title_slug:
            title = None
        last_text = None
        print('Wiki page {}'.format(page['slug']))
        for version in page['versions']:
            text = fix_wiki_links(convert_description(version['text']))
            text = insert_wiki_attachments(text, file_map)
            text += attachment_str
            if title is not None:
                text = '# {}\n{}'.format(title, text)
            if text == last_text:
                continue
            last_text = text

            user = user_map[version['user_id']]
            user_str = SimpleGitClient.format_user(user['name'],
                user['mail'])
            repo.commit_file(fn, text, user_str, version['created_at'])
    repo.gc()


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


def open_clients(project_url, auth_token):
    client = GitlabClient(
        GitlabClient.project_to_api_url(args.project_url),
        args.auth_token
    )
    system_client = GitlabClient(
        GitlabClient.project_to_base_url(args.project_url),
        args.auth_token
    )
    return client, system_client


if __name__ == '__main__':
    # Copy attachments to file/<id>/<attachment>!
    parser = argparse.ArgumentParser()
    parser.add_argument('action',
        choices=('check-users', 'issues', 'wiki'))
    parser.add_argument('project_url')
    parser.add_argument('auth_token')
    parser.add_argument('source_file')
    args = parser.parse_args()

    data = load_data(args.source_file)
    client, system_client = open_clients(args.project_url,
        args.auth_token)

    if args.action in ('check-users', 'issues'):
        # check for missing users for issues and boards
        active_users = get_active_users(data['issues'], data['boards'])
        user_map = get_users(system_client)
        unknown_users = active_users - set(user_map.keys())
        if len(unknown_users) > 0:
            for user in sorted(unknown_users):
                print('Unknown user {}'.format(user))

        DEFAULT_USER_ID = 1
        spare_user_map = defaultdict(lambda: DEFAULT_USER_ID, user_map)

    if args.action in ('issues',):
        assert len(get_issues(client)) == 0, "Project is not empty!"

        # create issues with milestones
        create_milestones(client, data['milestones'])
        milestone_map = get_milestone_map(client, data['milestones'])
        create_issues(client, data['issues'], milestone_map,
            spare_user_map)

        # create issues + milestones for boards
        board_milestones = map_boards_to_milestones(data['boards'])
        create_milestones(client, board_milestones)
        board_milestone_map = get_milestone_map(client, board_milestones)
        convert_boards(client, data['boards'], board_milestone_map,
            spare_user_map)

    if args.action in ('wiki',):
        # create wiki git
        project_name = os.path.splitext(
            os.path.split(args.source_file)[1])[0]
        convert_wiki(data['wiki'], project_name, data['users'])
