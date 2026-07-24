import fcntl
import os
import uuid


GENERATION_FILE = 'projects.generation'
LOCK_FILE = 'projects.lock'


def _read_generation(path):
    try:
        with open(path, 'r') as generation_file:
            return generation_file.read()
    except FileNotFoundError:
        return ''


def current_generation(wf):
    return _read_generation(wf.datafile(GENERATION_FILE))


def invalidate_projects(wf):
    lock_path = wf.datafile(LOCK_FILE)
    generation_path = wf.datafile(GENERATION_FILE)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    with open(lock_path, 'a') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        temporary_path = '{}.{}'.format(generation_path, uuid.uuid4().hex)
        with open(temporary_path, 'w') as generation_file:
            generation_file.write(uuid.uuid4().hex)
        os.replace(temporary_path, generation_path)
        wf.cache_data('projects', None)


def store_projects(wf, generation, projects):
    lock_path = wf.datafile(LOCK_FILE)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    with open(lock_path, 'a') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if current_generation(wf) != generation:
            return False
        wf.cache_data('projects', projects)
        return True
