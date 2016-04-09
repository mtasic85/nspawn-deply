#!/usr/bin/env python
import os
import sys
import json
import time
import shlex
import random
import hashlib
import argparse

import paramiko


#
# util
#
def parse_uri(uri):
    if '@' in uri:
        user, host = uri.split('@')
    else:
        user = 'root'
        host = uri

    if ':' in host:
        host, port = host.split(':')
        port = int(port)
    else:
        host = host
        port = 22

    return user, host, port


def parse_ports(ports_str):
    ports = []

    for n in ports_str.split(','):
        if ':' in n:
            src_port, dest_port = map(int, n.split(':'))
        else:
            src_port, dest_port = None, int(n)

        ports.append((src_port, dest_port))

    return ports


#
# local
#
def load_local_config():
    filename = 'nspawn.local.conf'

    if os.path.exists(filename):
        with open(filename, 'r') as f:
            config = json.load(f)
    else:
        config = {}

    return config


def save_local_config(config):
    filename = 'nspawn.local.conf'

    with open(filename, 'w') as f:
        json.dump(config, f, indent=True)


#
# remote
#
def ssh_client(uri):
    # FIXME: unused port
    user, host, port = parse_uri(uri)
    client = paramiko.client.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    known_hosts_path = os.path.expanduser('~/.ssh/known_hosts')
    client.load_host_keys(known_hosts_path)
    client.connect(host, username=user)
    return client


def create_container(uri, container, verbose=False):
    # ssh client
    client = ssh_client(uri)

    # create machine dir
    command = 'mkdir -p "/var/lib/machines/{id}"'.format(**container)
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    err = stderr.read()
    stdin.close()
    
    if err:
        raise IOError(err)

    # wait until other pacman instances finish install
    while True:
        command = 'ls /var/lib/pacman/db.lck'
        if verbose: print('{!r}'.format(command))
        stdin, stdout, stderr = client.exec_command(command)
        out = stdout.read()
        stdin.close()

        if out != '/var/lib/pacman/db.lck':
            break

        print('Machine already using pacman, waiting 5 seconds...')
        time.sleep(5.0)

    # boostrap container
    machine_dir = '/var/lib/machines/{id}'.format(**container)
    command = 'pacstrap -c -d "{}" base vim openssh'.format(machine_dir)
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()
    
    if verbose:
        for line in iter(lambda: stdout.readline(2048), ""):
            print(line, end="")
    else:
        out = stdout.read()

    # resolv.conf
    command = ''.join([
        'echo "nameserver 8.8.8.8" > ',
        '{}/etc/resolv.conf'.format(machine_dir),
    ])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # enable systemd-network
    s = '/usr/lib/systemd/system/systemd-networkd.service'
    d = '/etc/systemd/system/multi-user.target.wants/systemd-networkd.service'
    command = 'ln -s "{}{}" "{}{}"'.format(machine_dir, s, machine_dir, d)
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # enable sshd
    s = '/usr/lib/systemd/system/sshd.service'
    d = '/etc/systemd/system/multi-user.target.wants/sshd.service'
    command = 'ln -s "{}{}" "{}{}"'.format(machine_dir, s, machine_dir, d)
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # patch sshd
    f = '#PermitRootLogin prohibit-password'
    t = 'PermitRootLogin yes'
    p = '{}/etc/ssh/sshd_config'.format(machine_dir)
    command = 'sed -i \'s/{}/{}/g\' "{}"'.format(f, t, p)
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read()
    err = stderr.read()
    stdin.close()
    # print('out: {!r}'.format(out))
    # print('err: {!r}'.format(err))

    # patch sshd
    f = '#PermitEmptyPasswords no'
    t = 'PermitEmptyPasswords yes'
    p = '{}/etc/ssh/sshd_config'.format(machine_dir)
    command = 'sed -i \'s/{}/{}/g\' "{}"'.format(f, t, p)
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read()
    err = stderr.read()
    stdin.close()
    # print('out: {!r}'.format(out))
    # print('err: {!r}'.format(err))

    # enable service
    command = 'systemctl enable systemd-nspawn@{}.service'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()
    
    # override service
    command = 'mkdir -p "/etc/systemd/system/systemd-nspawn@{}.service.d"'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # override service
    command = 'printf "[Service]\\nExecStart=\\nExecStart={}" >{}'.format(
        '/usr/bin/systemd-nspawn --quiet --keep-unit --boot --network-veth {} --machine={}'.format(
            ' '.join('--port={}:{}'.format(k, v) for k, v in container['ports'].items()),
            container['id'],
        ),
        '/etc/systemd/system/systemd-nspawn\@{}.service.d/override.conf'.format(container['id'])
    )

    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read()
    err = stderr.read()
    stdin.close()
    # print('out: {!r}'.format(out))
    # print('err: {!r}'.format(err))

    # demon-reload
    command = 'systemctl daemon-reload'
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read()
    err = stderr.read()
    stdin.close()
    # print('out: {!r}'.format(out))
    # print('err: {!r}'.format(err))

    # FIXME:
    # possibly run container

    # close ssh client
    client.close()


def destory_container(uri, container, verbose=False):
    # ssh client
    client = ssh_client(uri)

    # FIXME: should we stop here?
    # stop service
    command = 'systemctl stop systemd-nspawn@{}.service'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # disable service
    command = 'systemctl disable systemd-nspawn@{}.service'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # rm dir
    command = 'rm -r /var/lib/machines/{}'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # rm service
    command = 'rm -r /etc/systemd/system/systemd-nspawn@{}.service.d'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # close ssh client
    client.close()


def load_remote_config(uri, filename='nspawn.remote.conf'):
    # ssh client
    client = ssh_client(uri)

    command = 'cat "{}"'.format(filename)
    stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read()
    err = stderr.read()
    stdin.close()    
    if err: raise IOError(err)

    # close ssh client
    client.close()
    
    config = json.load(out)
    return config


def save_remote_config(uri, config, filename='nspawn.remote.conf'):
    # ssh client
    client = ssh_client(uri)

    _config = shlex.quote(json.dumps(config))
    command = 'echo {} > "{}"'.format(_config, filename)
    stdin, stdout, stderr = client.exec_command(command)
    err = stderr.read()
    stdin.close()
    if err: raise IOError(err)

    # close ssh client
    client.close()


def merge_remote_configs(configs):
    merged_machines = {}
    merged_projects = {}
    merged_containers = {}

    for config in configs:
        # merge machines
        machines = config.get('machines', {})
        merged_machines.update(machines)

        # merge projects
        projects = config.get('projects', {})
        merged_projects.update(projects)

        # merge containers
        containers = config.get('containers', {})
        merged_containers.update(containers)

    config = {
        'machines': merged_machines,
        'projects': merged_projects,
        'containers': merged_containers,
    }

    return config


def load_consensus_config(uri, filename='nspawn.remote.conf'):
    configs = []

    try:
        config = load_remote_config(uri)
    except Exception as e:
        config = {
            'machines': {},
            'projects': {},
            'containers': {},
        }

        return config

    machines = config.get('machines', {})

    for machine_id, machine in machines.items():
        machine_uri = '{user}@{host}'.format(**machine)

        try:
            config = load_remote_config(machine_uri)
        except Exception as e:
            continue

        configs.append(config)

    config = merge_remote_configs(configs)
    return config


def save_consensus_config(config, filename='nspawn.remote.conf'):
    machines = config.get('machines', {})
    
    for machine_id, machine in machines.items():
        try:
            machine_uri = '{user}@{host}'.format(**machine)
            save_remote_config(machine_uri, config)
        except Exception as e:
            err = 'Error saving config on {} with machine id {}.'.format(
                machine['host'],
                machine_id,
            )

            print(err, file=sys.stderr)


def find_available_machine(config, container):
    machines = config['machines']
    containers = config['containers']
    machine_id = None
    b = False

    for m_id, m in machines.items():
        for c_id, c in containers.items():
            if c['name'] != container['name']:
                machine_id = m_id
                b = True
                break

        if b:
            break
    else:
        m_id, m = list(machines.items())[0]
        machine_id = m_id

    machine = machines[machine_id]
    return machine


def find_available_machine_port(config, machine, dest_port):
    containers = {
        n: m
        for n, m in config['containers'].items()
        if m['machine_id'] == machine['id']
    }

    containers_ports_map = {}

    for container_id, container in containers.items():
        for c_src_port, c_dest_port in container['ports'].items():
            containers_ports_map[c_src_port] = c_dest_port

    port = dest_port

    if port < 10000:
        port += 10000

    while port in containers_ports_map:
        port += 1

    return port


def find_available_machine_ports(config, machine, ports):
    available_ports_map = {}

    for src_port, dest_port in ports:
        if not src_port:
            src_port = find_available_machine_port(config, machine, dest_port)

        available_ports_map[src_port] = dest_port

    return available_ports_map


#
# config
#
def config_config(section, property_, value=None):
    config = load_local_config()

    if section not in config:
        config[section] = {}

    if value:
        config[section][property_] = value    
        save_local_config(config)
    else:
        value = config[section][property_]
        print(value)


#
# machine
#
def machine_list(remote_uri, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    remote_config = load_consensus_config(remote_uri)
    machine_items = remote_config.get('machines', {}).items()
    machine_items = sorted(
        list(machine_items),
        key=lambda n: (n[1]['host'], n[1]['port'])
    )
    print('{a: <12} {b: <67}'.format(a='MACHINE_ID', b='ADDRESS'))

    for machine_id, machine in machine_items:
        print('{a: <12} {b: <67}'.format(
            a=machine['id'],
            b='{}@{}:{}'.format(
                machine['user'],
                machine['host'],
                machine['port'],
            ),
        ))


def machine_add(remote_uri, uri, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    remote_user, remote_host, remote_port = parse_uri(remote_uri)
    user, host, port = parse_uri(uri)
    config = load_consensus_config(remote_uri)
    machines = config['machines']

    # check if host already exists
    for machine_id, machine in machines.items():
        if host == machine['host']:
            msg = 'Machine with host {} already exists'.format(host)
            print(msg, file=sys.stderr)
            sys.exit(1)

    # generate random ID
    m = hashlib.sha1()
    m.update('{}'.format(random.randint(0, 2 ** 128)).encode())
    machine_id = m.hexdigest()[-12:]

    machine = {
        'id': machine_id,
        'user': user,
        'host': host,
        'port': port,
    }

    machines[machine_id] = machine
    save_consensus_config(config)
    print('{} {}@{}:{}'.format(machine_id, user, host, port))


def machine_remove(remote_uri, machine_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    remote_user, remote_host, remote_port = parse_uri(remote_uri)
    config = load_consensus_config(remote_uri)
    machines = config['machines']

    if machine_id not in machines:
        msg = 'Machine with id {} does not exists'.format(machine_id)
        print(msg, file=sys.stderr)
        sys.exit(1)

    del machines[machine_id]
    save_consensus_config(config)
    print('{}'.format(machine_id))


#
# project
#
def project_list(remote_uri, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    remote_config = load_consensus_config(remote_uri)
    project_items = remote_config.get('projects', {}).items()
    project_items = list(project_items)
    project_items = sorted(project_items, key=lambda n: n[1]['name'])
    print('{a: <12} {b: <67}'.format(a='PROJECT_ID', b='NAME'))

    for project_id, project in project_items:
        print('{a: <12} {b: <67}'.format(
            a=project['id'],
            b='{}'.format(project['name']),
        ))


def project_add(remote_uri, project_name, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    remote_user, remote_host, remote_port = parse_uri(remote_uri)
    config = load_consensus_config(remote_uri)
    projects = config['projects']

    # check if project name already exists
    for project_id, project in projects.items():
        if project_name == project['name']:
            msg = 'Project with name {} already exists'.format(project_name)
            print(msg, file=sys.stderr)
            sys.exit(1)

    # generate random ID
    m = hashlib.sha1()
    m.update('{}'.format(random.randint(0, 2 ** 128)).encode())
    project_id = m.hexdigest()[-12:]

    project = {
        'id': project_id,
        'name': project_name,
    }

    projects[project_id] = project
    save_consensus_config(config)
    print('{} {}'.format(project_id, project_name))


def project_remove(remote_uri, project_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    remote_user, remote_host, remote_port = parse_uri(remote_uri)
    config = load_consensus_config(remote_uri)
    projects = config['projects']

    if project_id not in projects:
        msg = 'Project with id {} does not exists'.format(project_id)
        print(msg, file=sys.stderr)
        sys.exit(1)

    del projects[project_id]
    save_consensus_config(config)
    print('{}'.format(project_id))


#
# container
#
def container_list(remote_uri, project_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    if not project_id:
        local_config = load_local_config()
        project_id = local_config['main']['project_id']

    remote_config = load_consensus_config(remote_uri)
    container_items = remote_config.get('containers', {}).items()
    container_items = [
        n
        for n in container_items
        if n[1]['project_id'].endswith(project_id)
    ]
    container_items = sorted(
        container_items,
        key=lambda n: (n[1]['name'], n[1]['host'], n[1]['ports']),
    )
    
    print('{a: <12} {b: <10} {c: <15} {d: <33} {e: <6}'.format(
        a='CONTAINER_ID',
        b='NAME',
        c='ADDRESS',
        d='PORTS',
        e='STATUS',
    ))

    for container_id, container in container_items:
        status = 'x'
        
        ports_str = ','.join(
            '{}:{}'.format(k, v)
            for k, v in sorted(
                list(container['ports'].items()),
                key=lambda n: n[1],
            )
        )

        print('{a: <12} {b: <10} {c: <15} {d: <33} {e: <6}'.format(
            a=container_id,
            b=container['name'][:10],
            c=container['host'],
            d=ports_str[:33],
            e=status,
        ))


def container_add(remote_uri, project_id, uri, name, ports_str, distro, image_id, image, start=False, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']
    
    if not project_id:
        local_config = load_local_config()
        project_id = local_config['main']['project_id']

    remote_user, remote_host, remote_port = parse_uri(remote_uri)
    config = load_consensus_config(remote_uri)
    containers = config['containers']

    # parse ports
    requested_ports = parse_ports(ports_str)

    # check if project id exists
    projects = config['projects']
    
    if project_id not in projects:
        msg = 'Project with id {} does not exists'.format(project_id)
        print(msg, file=sys.stderr)
        sys.exit(1)

    # generate random ID
    m = hashlib.sha1()
    m.update('{}'.format(random.randint(0, 2 ** 128)).encode())
    container_id = m.hexdigest()[-12:]

    # init container
    container = {
        'id': container_id,
        'project_id': project_id,
        'host': remote_host,
        'name': name,
        'distro': distro,
        'image_id': image_id,
        'image': image,
    }

    # find suitable machine where to host container
    machine = find_available_machine(config, container)
    container['machine_id'] = machine['id']

    # find available ports
    ports = find_available_machine_ports(config, machine, requested_ports)
    container['ports'] = ports
    
    # create systemd-nspawn container on machine
    uri = '{user}@{host}'.format(**machine)
    create_container(uri, container, verbose)
    containers[container_id] = container
    save_consensus_config(config)

    # output on success
    print('{} {} {}'.format(
        container_id,
        machine['host'],
        ','.join('{}:{}'.format(k, v) for k, v in ports.items())
    ))


def container_remove(remote_uri, project_id, container_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    if not project_id:
        local_config = load_local_config()
        project_id = local_config['main']['project_id']

    remote_user, remote_host, remote_port = parse_uri(remote_uri)
    config = load_consensus_config(remote_uri)
    containers = config['containers']

    # FIXME:
    # check if container is running
    # if yes, stop it first

    # check if project id exists
    projects = config['projects']

    if project_id not in projects:
        msg = 'Project with id {} does not exists'.format(project_id)
        print(msg, file=sys.stderr)
        sys.exit(1)

    project = projects[project_id]

    if container_id not in containers:
        msg = 'Container with id {} does not exists'.format(container_id)
        print(msg, file=sys.stderr)
        sys.exit(1)

    container = containers[container_id]

    # machine
    machines = config['machines']
    machine = machines[container['machine_id']]

    # create systemd-nspawn container on machine
    uri = '{user}@{host}:{port}'.format(**machine)
    destory_container(uri, container, verbose)
    del containers[container_id]
    save_consensus_config(config)

    # close ssh client
    client.close()

    print('{}'.format(container_id))


def container_start(remote_uri, project_id, container_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    if not project_id:
        local_config = load_local_config()
        project_id = local_config['main']['project_id']

    config = load_consensus_config(remote_uri)
    containers = config['containers']
    container = containers[container_id]

    # ssh client
    client = ssh_client(remote_uri)

    # start service
    # systemctl start systemd-nspawn@8747d5dd3f96c84f4160165ad2fc1ed33fa5209b.service
    command = 'systemctl start systemd-nspawn@{}.service'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # close ssh client
    client.close()


def container_stop(remote_uri, project_id, container_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    if not project_id:
        local_config = load_local_config()
        project_id = local_config['main']['project_id']

    config = load_consensus_config(remote_uri)
    containers = config['containers']
    container = containers[container_id]

    # ssh client
    client = ssh_client(remote_uri)

    # stop service
    # systemctl stop systemd-nspawn@8747d5dd3f96c84f4160165ad2fc1ed33fa5209b.service
    command = 'systemctl stop systemd-nspawn@{}.service'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # close ssh client
    client.close()


def container_restart(remote_uri, project_id, container_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    if not project_id:
        local_config = load_local_config()
        project_id = local_config['main']['project_id']

    config = load_consensus_config(remote_uri)
    containers = config['containers']
    container = containers[container_id]

    # ssh client
    client = ssh_client(remote_uri)

    # restart service
    # systemctl restart systemd-nspawn@8747d5dd3f96c84f4160165ad2fc1ed33fa5209b.service
    command = 'systemctl restart systemd-nspawn@{}.service'.format(container['id'])
    if verbose: print('{!r}'.format(command))
    stdin, stdout, stderr = client.exec_command(command)
    stdin.close()

    # close ssh client
    client.close()


def container_migrate(remote_uri, project_id, container_id, verbose=False):
    if not remote_uri:
        local_config = load_local_config()
        remote_uri = local_config['main']['remote_address']

    if not project_id:
        local_config = load_local_config()
        project_id = local_config['main']['project_id']

    config = load_consensus_config(remote_uri)
    containers = config['containers']
    container = containers[container_id]

    # ssh client
    client = ssh_client(remote_uri)

    raise NotImplementedError


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='systemd-nspawn deployment')
    parser_subparsers = parser.add_subparsers(dest='subparser', metavar='main')
    parser.add_argument('--remote-address', '-r', help='Remote address')

    # config
    config_parser = parser_subparsers.add_parser('config')
    config_parser.add_argument('--section', '-s', default='main', help='Section')
    config_parser.add_argument('--property', '-p', help='Propery')
    config_parser.add_argument('--value', '-v', help='Value')
    
    # machine
    machine_parser = parser_subparsers.add_parser('machine')
    machine_subparsers = machine_parser.add_subparsers(dest='machine_subparser', metavar='machine')
    
    # machine list
    machine_list_parser = machine_subparsers.add_parser('list', help='List machines')

    # machine add
    machine_add_parser = machine_subparsers.add_parser('add', help='Add machine')
    machine_add_parser.add_argument('--address', '-a', help='[USER="root"@]HOST[:PORT=22]')

    # machine remove
    machine_remove_parser = machine_subparsers.add_parser('remove', help='Remove machine')
    machine_remove_parser.add_argument('--id', '-I', help='Machine ID')
    
    # project
    project_parser = parser_subparsers.add_parser('project')
    project_subparsers = project_parser.add_subparsers(dest='project_subparser', metavar='project')

    # project list
    project_list_parser = project_subparsers.add_parser('list', help='List projects')

    # project add
    project_add_parser = project_subparsers.add_parser('add', help='Add project')
    project_add_parser.add_argument('--id', '-I', default=None, help='Project ID')
    project_add_parser.add_argument('--name', '-n', help='Name')

    # project remove
    project_remove_parser = project_subparsers.add_parser('project', help='Remove project')
    project_remove_parser.add_argument('--id', '-I', help='Project ID')

    # container 
    container_parser = parser_subparsers.add_parser('container')
    container_subparsers = container_parser.add_subparsers(dest='container_subparser', metavar='container')
    container_parser.add_argument('--project-id', '-P', help='Project ID')
    
    # container list
    container_list_parser = container_subparsers.add_parser('list', help='List of containers at remote host')

    # container add
    container_add_parser = container_subparsers.add_parser('add', help='Add container')
    container_add_parser.add_argument('--address', '-a', default=None, help='[USER="root"@]HOST[:PORT=22]')
    container_add_parser.add_argument('--name', '-n', help='Human readable name of container')
    container_add_parser.add_argument('--ports', '-p', help='MACHINE_PORT:CONTAINER_PORT[,M_PORT:C_PORT,...]')
    container_add_parser.add_argument('--distro', '-d', default='arch', help='Linux distribution: arch, debian, fedora')
    container_add_parser.add_argument('--image-id', '-I', help='Image ID')
    container_add_parser.add_argument('--image', '-i', help='Image')
    container_add_parser.add_argument('--start', '-s', action='store_true', help='Start container')
    container_add_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose')

    # container remove
    container_remove_parser = container_subparsers.add_parser('remove', help='Remove container')
    container_remove_parser.add_argument('--id', '-I', help='Container ID')
    container_remove_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose')

    # container start
    container_start_parser = container_subparsers.add_parser('start', help='Start container')
    container_start_parser.add_argument('--id', '-I', help='Container ID')
    container_start_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose')

    # container stop
    container_stop_parser = container_subparsers.add_parser('stop', help='Stop container')
    container_stop_parser.add_argument('--id', '-I', help='Container ID')
    container_stop_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose')

    # container restart
    container_restart_parser = container_subparsers.add_parser('restart', help='Restart container')
    container_restart_parser.add_argument('--id', '-I', help='Container ID')
    container_restart_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose')

    # parse args
    args = parser.parse_args()
    # print(args)

    if args.subparser == 'config':
        config_config(args.section, args.property, args.value)
    elif args.subparser == 'machine':
        if args.machine_subparser == 'list':
            machine_list(args.remote_address)
        elif args.machine_subparser == 'add':
            machine_add(args.remote_address, args.address)
        elif args.machine_subparser == 'remove':
            machine_remove(args.remote_address, args.id)
    elif args.subparser == 'project':
        if args.project_subparser == 'list':
            project_list(args.remote_address)
        elif args.project_subparser == 'add':
            project_add(args.remote_address, args.name)
        elif args.project_subparser == 'remove':
            project_remove(args.remote_address, args.id)
    elif args.subparser == 'container':
        if args.container_subparser == 'list':
            container_list(args.remote_address, args.project_id)
        elif args.container_subparser == 'add':
            container_add(
                args.remote_address,
                args.project_id,
                args.address,
                args.name,
                args.ports,
                args.distro,
                args.image_id,
                args.image,
                args.start,
                args.verbose,
            )
        elif args.container_subparser == 'remove':
            container_remove(
                args.remote_address,
                args.project_id,
                args.id,
                args.verbose,
            )
        elif args.container_subparser == 'start':
            container_start(args.remote_address, args.project_id, args.id)
        elif args.container_subparser == 'stop':
            container_stop(args.remote_address, args.project_id, args.id)
        elif args.container_subparser == 'restart':
            container_restart(args.remote_address, args.project_id, args.id)
        elif args.container_subparser == 'migrate':
            container_migrate(args.remote_address, args.project_id, args.id)
