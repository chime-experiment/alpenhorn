# Alpenhorn

Alpenhorn is a daemon and client for managing CHIME archive data.

## Migrating from bitbucket

This repository used to live on bitbucket.  If you have an old clone of the
repository from bitbucket, you can migrate to this new version here by
executing from within your copy:

```sh
git remote set-url --add origin git@github.com:chime-experiment/alpenhorn.git
git remote set-url --delete origin git@bitbucket.org:chime/alpenhorn.git
```

## (Re-)Installing on `tubular` and `jingle`
After making changes to alpenhorn, a CHIME administrator will have to
re-install this package on the production servers using
[ch\_ansible](https://bitbucket.org/chime/ch_ansible).  To re-install
only the alpenhorn-specific hosts, you can run:
```sh
ansible-playbook -K --limit=alpenhorn -i ../production.yaml playbook.yaml
```
from the `plays/` subdirectory of `ch_ansible`.

If you want to restart `alpenhornd` after updating, use this instead:
```sh
ansible-playbook -K --limit=alpenhorn -e restart_alpenhorn=true -i ../production.yaml playbook.yaml
```
