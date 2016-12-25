#!/usr/bin/env python3
import mysql.connector
import sys


def open_database_connection():
    return mysql.connector.connect(user='openproject',
            password='password', database='openproject',
            host='localhost')


def get_users(dbcon):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id`, `login`, `firstname`, `lastname`, "
                "`mail`, `status` FROM `users`")
        data = cur.fetchall()
    finally:
        cur.close()

    results = {}
    for row in data:
        # statuses: builtin: 0, active: 1, registered: 2, locked: 3, invited: 4
        if not row[5] in (1, 3):
            continue
        results[row[0]] = {
            'login': row[1],
            'name': row[2] + " " + row[3],
            'mail': row[4],
            'is_locked': row[5] == 3
        }
    return results


def get_project_id(dbcon, identifier):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id` FROM `projects` "
                "WHERE `identifier` = %s",
                (identifier,))
        data = cur.fetchone()
    finally:
        cur.close()
    if data is None:
        raise ValueError("Unknown identifier {}".format(identifier))
    return data[0]


def get_issue_milestones(dbcon, project_id):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id`, `name`, `description`, `start_date`, "
                "`effective_date`, `status` FROM `versions`"
                "WHERE project_id = %s",
                (project_id,))
        data = cur.fetchall()
    finally:
        cur.close()
    results = {}
    for row in data:
        results[row[0]] = {
            'name': row[1],
            'description': row[2],
            'start_date': row[3],
            'end_date': row[4],
            'is_closed': row[5] == 'closed'
        }
    return results


def get_issue_types(dbcon):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id`, `name` FROM `types`")
        data = cur.fetchall()
    finally:
        cur.close()
    results = {}
    for row in data:
        if row[1] == 'none':
            continue
        results[row[0]] = {
            'name': row[1]
        }
    return results


def get_issue_categories(dbcon, project_id):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id`, `name` FROM `categories`"
                "WHERE project_id = %s",
                (project_id,))
        data = cur.fetchall()
    finally:
        cur.close()
    results = {}
    for row in data:
        results[row[0]] = {
            'name': row[1]
        }
    return results


def get_issue_statuses(dbcon):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id`, `name`, `is_closed` FROM `statuses`")
        data = cur.fetchall()
    finally:
        cur.close()
    results = {}
    for row in data:
        results[row[0]] = {
            'name': row[1],
            'is_closed': row[2] == 1,
            'add_label': row[1] == 'rejected'
        }
    return results


def convert_issue_results(row, category_map, status_map, type_map,
        user_map):
    issue = {
        'title': row[1],
        'description': row[2],
        'assignee_id': row[3],
        'milestone_id': row[4],
        'labels': [type_map[row[6]]['name']],
        'is_closed': status_map[row[7]]['is_closed'],
        'author_id': user_map[row[8]]['login'],
        'created_at': row[9],
        'start_date': row[10],
        'due_date': row[11],
        'notes': row[12]
    }
    if row[5] is not None:
        issue['labels'].append(category_map[row[5]]['name'])
    if status_map[row[7]]['add_label']:
        issue['labels'].append(status_map[row[7]]['name'])
    return issue


def iter_issue_actions(issue):
    for action in reversed(issue['actions']):
        yield action
    yield issue


def deduplicate_issue_action(issue, new_action, protected_attributes):
    for attribute in list(new_action.keys()):
        if attribute in protected_attributes:
            continue
        # compare with latest occurence of this attribute
        for action in iter_issue_actions(issue):
            if attribute in action:
                if new_action[attribute] == action[attribute]:
                    # delete attribute if unchanged
                    del new_action[attribute]
                break

    if set(new_action.keys()) == set(protected_attributes):
        # no change made by this action
        return None
    return new_action


def get_issues(dbcon, project_id, category_map, status_map, type_map,
        user_map):
    cur = con.cursor()
    try:
        cur.execute("SELECT j.`journable_id` as `id`, w.`subject`, "
                "w.`description`, w.`assigned_to_id`, "
                "w.`fixed_version_id`, w.`category_id`, w.`type_id`, "
                "w.`status_id`, j.`user_id`, j.`created_at`, "
                "w.`start_date`, w.`due_date`, j.`notes` "
                "FROM `work_package_journals` w "
                "INNER JOIN `journals` j ON w.`journal_id` = j.`id` "
                "WHERE w.`project_id` = %s "
                "ORDER BY j.`journable_id` ASC",
                (project_id,))
        data = cur.fetchall()
    finally:
        cur.close()
    results = {}
    for row in data:
        if row[0] not in results:
            issue = convert_issue_results(row, category_map,
                    status_map, type_map, user_map)
            del issue['notes']
            issue['actions'] = []
            issue['watcher_ids'] = []
            issue['relations'] = []
            results[row[0]] = issue
        else:
            action = convert_issue_results(row, category_map,
                    status_map, type_map, user_map)
            if len(action['notes']) == 0:
                del action['notes']
            del action['start_date']
            issue = results[row[0]]
            action = deduplicate_issue_action(issue, action,
                    ('author_id', 'created_at'))
            if action is not None:
                issue['actions'].append(action)

    cur = con.cursor()
    try:
        # can't easily filter by project
        cur.execute("SELECT `watchable_id`, `user_id` FROM `watchers` "
                "WHERE `watchable_type` = 'WorkPackage'")
        data = cur.fetchall()
    finally:
        cur.close()

    for row in data:
        if row[0] in results:
            results[row[0]]['watcher_ids'].append(row[1])

    cur = con.cursor()
    try:
        # can't easily filter by project
        cur.execute("SELECT `from_id`, `to_id`, `relation_type` "
                "FROM `relations`")
        data = cur.fetchall()
    finally:
        cur.close()

    for row in data:
        if row[0] in results:
            results[row[0]]['relations'].append((row[2], row[1]))
        if row[1] in results:
            results[row[1]]['relations'].append((row[2] + "_inv", row[0]))

    return results


def get_board_messages(dbcon, board_id, user_map):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id`, `parent_id`, `subject`, `content`, "
                "`author_id`, `created_on`, `locked` "
                "FROM `messages` WHERE `board_id` = %s",
                (board_id,))
        data = cur.fetchall()
    finally:
        cur.close()
    results = {}
    for row in data:
        if row[1] is None:
            results[row[0]] = {
                'title': row[2],
                'description': row[3],
                'assignee_id': None,
                'milestone_id': None,
                'labels': ['Discussion'],
                'is_closed': row[6] == 1,
                'author_id': user_map[row[4]]['login'],
                'created_at': row[5],
                'start_date': None,
                'due_date': None,
                'actions': [],
                'watcher_ids': [],
                'relations': []
            }
        else:
            results[row[1]]['actions'].append({
                'author_id': user_map[row[4]]['login'],
                'created_at': row[5],
                'notes': row[3]
            })

    return results


def get_boards(dbcon, project_id, user_map):
    cur = con.cursor()
    try:
        cur.execute("SELECT `id`, `name` FROM `boards`"
                "WHERE project_id = %s",
                (project_id,))
        data = cur.fetchall()
    finally:
        cur.close()
    results = {}
    for row in data:
        results[row[0]] = {
            'name': row[1],
            'issues': get_board_messages(dbcon, row[0], user_map)
        }
    return results


try:
    con = open_database_connection()
    users = get_users(con)
    print("Found {} users".format(len(users)))
    project_id = get_project_id(con, 'regelung')
    print("Project id {}".format(project_id))
    milestones = get_issue_milestones(con, project_id)
    print("Milestones:")
    for (mid, milestone) in milestones.items():
        print("    {}: {}".format(mid, milestone))
    types = get_issue_types(con)
    print("Types {}".format(types))
    categories = get_issue_categories(con, project_id)
    print("Categories {}".format(categories))
    statuses = get_issue_statuses(con)
    print("Statuses {}".format(statuses))
    issues = get_issues(con, project_id, categories, statuses, types,
            users)
    print("Issues")
    for (iid, issue) in issues.items():
        print("    {} {}".format(iid, issue))
    boards = get_boards(con, project_id, users)
    print("Boards")
    for (board_id, board) in boards.items():
        print("    {} {}".format(board_id, board['name']))
        for (iid, issue) in board['issues'].items():
            print("        {} {}".format(iid, issue))

except mysql.connector.Error as e:
    print("Error {}: {}".format(e.args[0], e.args[1]))
    sys.exit(1)

finally:
    if con:
        con.close()
