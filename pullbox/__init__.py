import os
import sys
import time
import shlex
import logging
import logging.handlers
import tempfile
import datetime
import argparse
import threading
import subprocess
from distutils.spawn import find_executable

import filelock
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# prevent watchdog module from writing DEBUG logs
# as that is adding too much confusion during debugging
logging.getLogger('watchdog').setLevel(logging.WARNING)

DEFAULT_LOCK_FILE = os.path.join(tempfile.gettempdir(), 'pullbox.lock')

class PullboxException(Exception): pass

class PullboxCalledProcessError(PullboxException):
    def __init__(self, cmd, retcode):
        self.cmd = cmd
        self.retcode = retcode

    def __str__(self):
        return 'PullboxCalledProcessError(code=%s, cmd="%s")' % \
            (self.retcode, self.cmd)

    __unicode__ = __str__
    __repr__ = __str__

LOG_FORMATTER = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
LOG_DEFAULT_FNAME = 'log.pullbox'
MAX_LOG_FILE_SIZE = 10 * 1024 * 1024 # 10MB

def init_logger(fname, log_level, quiet=False):
    log = logging.getLogger('')

    stderr_hdlr = logging.StreamHandler(sys.stderr)
    rofile_hdlr = logging.handlers.RotatingFileHandler(fname,
        maxBytes=MAX_LOG_FILE_SIZE, backupCount=10)
    hdlrs = (stderr_hdlr, rofile_hdlr)

    for hdlr in hdlrs:
        hdlr.setFormatter(LOG_FORMATTER)
        log.addHandler(hdlr)

    log.addHandler(rofile_hdlr)
    if not quiet: log.addHandler(stderr_hdlr)

    log.setLevel(getattr(logging, log_level.upper()))

    return log

class LocalFSEventHandler(FileSystemEventHandler):
    def __init__(self, on_change):
        self.on_change = on_change

    def on_any_event(self, evt):
        is_git_dir = '.git' in evt.src_path.split(os.path.sep)
        is_dot_file = os.path.basename(evt.src_path).startswith('.')
        is_dir_modified = evt.event_type == 'modified' and evt.is_directory

        if not (is_git_dir or is_dot_file or is_dir_modified):
            self.on_change()

class Pullbox(object):
    BINARIES_NEEDED = ['git', 'ssh']
    BINARIES_NEEDED_REMOTE = ['git', 'inotifywait']

    # Frequency at which client must poll data
    # server for data changes (if any)
    POLL_INTERVAL = 60 # seconds

    def __init__(self, server, path, log):
        self.server = server
        self.path = os.path.abspath(path)
        self.log = log

        self.dirname = os.path.basename(path.rstrip(os.path.sep))

        # Setup monitoring of local repo changes
        self.fs_observer = Observer()
        self.fs_observer.schedule(LocalFSEventHandler(self.on_fs_change),
            path, recursive=True)

        self.fs_changed = True

        # time at which changes (if any) need to downloaded
        # from remote repo
        self.next_pull_at = 0

    def on_fs_change(self):
        self.fs_changed = True

    def invoke_process(self, cmd, ignore_code=0):
        self.log.debug('invoke_process(%s)' % cmd)

        devnull = open(os.devnull, 'w')
        r = subprocess.call(shlex.split(cmd), stdout=devnull, stderr=devnull)

        if r == 130:
            raise KeyboardInterrupt

        if not isinstance(ignore_code, (list, tuple)):
            ignore_code = [ignore_code]

        if r != 0 and r not in ignore_code:
            raise PullboxCalledProcessError(cmd, r)

    def check_binaries(self):
        self.log.debug('Checking presence of local binaries "%s"' % \
            ', '.join(self.BINARIES_NEEDED))

        for binf in self.BINARIES_NEEDED:
            if not find_executable(binf):
                raise PullboxException('"%s" binary required' % binf)

    def check_remote_binaries(self):
        self.log.debug('Checking presence of remote binaries "%s"' % \
            ', '.join(self.BINARIES_NEEDED_REMOTE))

        for binf in self.BINARIES_NEEDED_REMOTE:
            cmd = 'ssh %s which %s' % (self.server, binf)
            try:
                self.invoke_process(cmd)
            except PullboxCalledProcessError:
                raise PullboxException(
                    '"%s" remote binary required (or) '
                    'could not connect to server' % binf)

    def ensure_remote_repo(self):
        # git init creates a new repo if none exists else
        # "reinitializes" which is like a no-op for our purposes
        cmd = 'ssh %s git init --bare %s' % (self.server,
            self.dirname)
        self.invoke_process(cmd)

    def keeprunning(self, fn, wait=0, error_wait=1):
        '''
        Keep @fn running on success or failure in an infinite loop
         - On failure, log exception, wait for @error_wait seconds
         - On success, wait for @wait seconds
        '''

        while 1:
            try:
                fn()
            except (SystemExit, KeyboardInterrupt): raise
            except:
                self.log.exception('During run of "%s" func' % fn.func_name)
                time.sleep(error_wait)
                continue

            time.sleep(wait)

    def track_remote_changes(self):
        cmd = 'ssh %s inotifywait -rqq -e modify -e move -e create -e delete %s' % \
            (self.server, self.dirname)
        self.invoke_process(cmd)
        self.next_pull_at = time.time()

    def init_local_repo(self):
        bpath = os.path.dirname(self.path.rstrip(os.path.sep))

        if not os.path.exists(bpath):
            os.makedirs(bpath)

        cwd = os.getcwd()
        try:
            os.chdir(bpath)
            self.invoke_process('git clone %s:%s' % (self.server, self.dirname))
            # add a dummy file to avoid trouble
            os.chdir(self.path)
            self.invoke_process('touch README.md')
            self.invoke_process('git add README.md')
            self.invoke_process('git commit -a -m "initial"')
            self.invoke_process('git push origin master')
        finally:
            os.chdir(cwd)

    def pull_changes(self):
        if self.next_pull_at > time.time(): return

        if not os.path.exists(self.path):
            self.init_local_repo()

        cwd = os.getcwd()
        try:
            os.chdir(self.path)
            self.invoke_process('git pull')
        finally:
            os.chdir(cwd)

        self.next_pull_at = time.time() + self.POLL_INTERVAL

    def push_changes(self):
        if not self.fs_changed: return

        cwd = os.getcwd()
        try:
            os.chdir(self.path)
            self.invoke_process('git add .')

            dt = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')
            msg = 'auto commit at %s' % dt
            self.invoke_process('git commit -a -m "%s"' % msg, ignore_code=1)

            self.invoke_process('git push origin master', ignore_code=-2)
        finally:
            os.chdir(cwd)

        self.fs_changed = False

    def run_thread(self, target):
        t = threading.Thread(target=target)
        t.daemon = True
        t.start()
        return t

    def start(self):
        # ensure required binaries are available in PATH
        self.check_binaries()
        self.check_remote_binaries()

        # ensure remote git repo is present (if not init one)
        self.ensure_remote_repo()

        # ensure local repo has latest data from server
        self.pull_changes()

        # start listening for changes in local repo
        self.fs_observer.start()

        # start threads
        K = self.keeprunning
        R = self.run_thread

        self.thread_track_remote_changes = R(lambda: K(self.track_remote_changes))
        self.thread_pull_changes = R(lambda: K(self.pull_changes, wait=0.1))
        self.thread_push_changes = R(lambda: K(self.push_changes, wait=0.1))

        # wait for threads to complete
        self.thread_track_remote_changes.join()
        self.thread_pull_changes.join()
        self.thread_push_changes.join()

def main():
    parser = argparse.ArgumentParser(description='Pullbox')

    parser.add_argument('path', help='Path to data directory')
    parser.add_argument('server', help='IP/Domain name of backup server')

    parser.add_argument('--log', default=LOG_DEFAULT_FNAME,
        help='Name of log file')
    parser.add_argument('--log-level', default='WARNING',
        help='Logging level as picked from the logging module')
    parser.add_argument('--quiet', action='store_true')

    parser.add_argument('--lock-file', default=DEFAULT_LOCK_FILE,
        help='Lock file to prevent multiple instances from running')

    args = parser.parse_args()
    lock = filelock.FileLock(args.lock_file)

    try:
        with lock.acquire(timeout=0):
            log = init_logger(args.log, args.log_level, quiet=args.quiet)
            p = Pullbox(args.server, args.path, log)
            p.start()
    except (SystemExit, KeyboardInterrupt): sys.exit(1)
    except Exception, e:
        log = logging.getLogger('')
        log.exception('exiting process because of exception')
        print >> sys.stderr, str(e)
        sys.exit(1)

    sys.exit(0)

if __name__ == '__main__':
    main()
