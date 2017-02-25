# Openproject-Gitlab-Migrator
Extracts issues, forum posts, wiki pages and meetings from OpenProject and adds
them to Gitlab. Forum posts are converted to issues, whereas meetings are converted
to wiki pages.

**Use at your own risk, create backups of OpenProject AND Gitlab BEFORE use!**

Tested using OpenProject 6.1.1 with the meetings add-on and Gitlab 8.15.3!
Requires admin privileges!

Inspired by https://github.com/oasiswork/redmine-gitlab-migrator/.

## Installation
Install the following dependencies. Both scripts can be run on separate computers

`dump_openproject.py`:
- python3, python3-mysql-connector

`import_to_gitlab.py`:
- python3, python3-requests
- git
- [pandoc](http://pandoc.org/installing.html) (use a relatively NEW version, tested with pandoc 1.19.1)


## Preparations
The `dump_openproject.py` script directly extracts data from the OpenProject
database. It is written against the database format used in OpenProject 6.1.1.
When using a newer OpenProject version, it is recommended to downgrade the
database. **WARNING: This will most likely cause some data loss! Do NOT proceed
without a WORKING backup!** The database should be downgraded to migration
*20161025135400*.

Use the following command to check the currently applied migrations.
```
RAILS_ENV='production' bundle exec rake db:migrate:status
```

This command will rollback _STEP_ migrations.
```
RAILS_ENV='production' bundle exec rake db:rollback STEP=1
```


## Dump OpenProject Data
Run the `dump_openproject.py` script once for every project you want to migrate.
This will create one _json_ file per project, which contains all data except the
attachments. The script directly extracts the necessary data from the openproject
database and thus needs to access it. It is advisable to use a copy of the
original database in order to avoid intermediate changes to the database.
```
./dump_openproject.py project_name db_host db_user db_password openproject_database
```


## Import in Gitlab
1. Copy attachments from /path/to/openproject/files/attachment to the same folder
   as the `import_to_gitlab.py` script. The file paths of the attachments
   relative to the script should be `./file/<id>/<attachment>`. Also copy the
   dumped _json_ files to the script folder.
2. Create Gitlab groups and projects
3. Choose to create the default labels for issues in the new projects
4. Create missing users with
```
./import_to_gitlab.py create-users https://gitlab-instance/group/project private_token project.json
```
   The private token of your account (**admin** rights are required!) can be
   obtained in Gitlab under
   User Icon (top right) > Settings > Account > Private Token.
   In order to associate users with LDAP uncomment and adapt the _create_user_
   function accordingly.
5. Add users to their projects
6. If a git repository is attached to the project, upload it now. This is also
   the right moment to convert a repository to use [Git LFS](https://github.com/git-lfs/git-lfs/wiki/Tutorial).

   If necessary create a copy of it using
```
git clone --mirror git@git-server:path/to/project.git
```
   Upload using
```
git push --mirror git@gitlab-instance:group/project.git
```
7. Import issues into Gitlab. Issue ids from OpenProject will be kept.
   **WARNING: All users involved in the issues will be temporarily granted the
   ADMIN permission. Double check that it got revoked once the issues have been
   imported.**

   **WARNING: If the script crashes while importing issues the admin permission
   will NOT be REVOKED**

```
./import_to_gitlab.py issues https://gitlab-instance/group/project private_token project.json
```
   The parameters are the same as used for the user creation.
8. **Check that the ADMIN permissions were restored properly!**
9. Convert wiki entries. This will create a git repository named `project.wiki.git`.
   Gitlab access is required to upload embedded images and attachments.
```
./import_to_gitlab.py wiki https://gitlab-instance/group/project private_token project.json
```
   Upload wiki repository to the Gitlab wiki repository.
```
git push --mirror git@gitlab-instance:group/project.wiki.git
```


## Design choices
- OpenProject and Gitlab permissions models differ to much for automatic conversion
- Only transfers users which are relevant for issues / forum posts / wiki
- Admin permission for users is required to set timestamps
- Work package versions are converted to milestones
- Issues relations / hierarchy are folded into the issue description
- Issues types / categories and statuses are converted to issue labels
- Forum posts are converted to issues with _discussion_ label
- Wiki page redirections are applied to the wiki pages
- Meetings are converted to wiki pages
- Not optimized for large projects. All data must fit in memory!


## Known Problems
- Issue creation is slow if issue ids contain large gaps. This is caused by
  the way issue ids are set: Gitlab offers no API to directly influence the issue
  ids. The ids start with 1 for a new project and increase with every new issue.
  The import script keeps creating and deleting temporary issues to skip gaps in
  the issues ids.
- Issue dates are not always retained correctly (Not sure why)
- References in issues are not always highlighted. This may be the case for
  commits referencing issues and for issues containing references to later issues
- Participants of meetings are not kept
- Dumping the database doesn't use transactions to get a consistent snapshot
- Admin permission is not revoked if the import script crashes
- Cross project wiki links will probably break
- No pagination handling for Gitlab users


## Used OpenProject database tables
- Users
  - users (login, mail, first+lastname)

- Project list
  - projects -> map project name to id

- Work packages
  - versions (milestones)
  - types (bug type -> label)
  - categories (name -> label)
  - statuses (open / closed / rejected)

  - work_package_journals
  - watchers
  - relations
  - hierarchy

- Forum
  - boards
  - messages + message_journals (-> issues with discussion label)

- File attachments
  - attachments (old files have already been deleted from disk -> not available for import)

- Wiki
  - wikis -> project_id to wiki_id
  - wiki_redirects -> directly modify wiki pages
  - wiki_content_journals + wiki_pages (title -> name)

- Meetings
  - meetings + meeting_contents (converted to wiki pages)
