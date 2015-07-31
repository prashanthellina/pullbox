# Pullbox

`Pullbox` is a very simple implementation that can serve as an alternative
for Dropbox that is based on Git. It works currently on any Linux-like OS
and OSX but not on Windows.

## Why?

Dropbox works well enough and works on many platforms. Although your data is
on someone else's server, it is probably safer over there than with you (for
most cases). I wrote `Pullbox` to overcome a specific limitation in Dropbox
i.e. Symlinks. Dropbox does not "see" symlinks. Although it synchronizes the
content pointed to by the symlink, it forgets that fact that it is a Symlink
when you sync to another computer.

I want to maintain my personal wiki and journal as plain text files. In
order to organize my notes structure, I depend on symlinks (so I can put the
same note under multiple directories). Dropbox does not support this
use-case.

## How does it work?

`Pullbox` needs SSH access to a remote Linux server that has `git` and
`inotifywait` commands installed. This serves as the backup location for
your local data.

`Pullbox` monitors file system activity in the local directory and
automatically pushes changes to the remote repo. The monitoring is done
using `inotify` on Linux, `FSEvents` on OSX, `kqueue` on BSD style OSs.

`Pullbox` also monitors file system activity on the remote repo and
automatically pulls changes to the local repo when needed. This is achieved
by using `ssh` and running `inotifywait` on the server (a lot like AJAX
long-polling except we use SSH here instead of HTTP).

## Setting up

### Backup Server

Instructions show below assume Ubuntu Linux. You can modify based on the
actual distro you have. Let us say the domain name of the backup server is
`example.com`

```bash
sudo apt-get install git inotify-tools
```

### Your local machine

```bash
sudo pip install git+git://github.com/prashanthellina/pullbox
```

I am assuming that the username on the backup server is `prashanth`. We need
to setup password-less SSH login to `prashanth@example.com` (instructions
[here](http://www.linuxproblem.org/art_9.html))

`Pullbox` depends on password-less login, so make sure it is working before
proceeding.

Let us assume that you local directory that you want to sync exists at
`/home/prashanth/notes`. You can run `Pullbox` manually by running the
following command.

```bash
pullbox --log-level DEBUG /home/prashanth/notes prashanth@example.com
```

That is right. Your directory will now be kept in sync with the remote
server repo as long as the `pullbox` command above runs. In order to have
the command run all the time (after system reboot and upon accidental
killing etc), put an entry in crontab like so

```bash
* * * * * pullbox --log-level DEBUG --log /tmp/pullbox.log --quiet /home/prashanth/notes prashanth@example.com &> /dev/null
```
