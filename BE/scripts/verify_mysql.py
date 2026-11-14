#!/usr/bin/env python3
"""Opt-in read-only MySQL regression. Never deletes containers or volumes."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COUNTS = {'users':5,'roles':4,'policies':14,'resources':5,'user_roles':4,'role_policies':16,'policy_resources':7,
                   'access_pages':2,'access_endpoints':1,'access_fields':3,'page_endpoints':2,'context_policy_rules':9}
EXPECTED_HISTORY = [('1','V1__access_schema.sql'),('2','V2__local_demo_seed.sql'),
                    ('3','V3__management_schema.sql'),('4','V4__local_management_seed.sql'),
                    ('5','V5__context_access_schema.sql'),('6','V6__local_context_access_seed.sql'),('7','V7__login_accounts_schema.sql')]

def parse_snapshot(text):
    counts, history = {}, []
    for line in text.splitlines():
        fields=line.split('\t')
        if len(fields)==2 and fields[0] in EXPECTED_COUNTS:
            if fields[0] in counts: raise ValueError('Duplicate count row: '+fields[0])
            counts[fields[0]]=int(fields[1])
        elif len(fields)==4:
            version,script,checksum,success=fields
            if success!='1' or not checksum or checksum=='NULL': raise ValueError('Unsuccessful/unchecksummed migration')
            history.append((version,script,int(checksum),success))
        else: raise ValueError('Unexpected snapshot row shape')
    if counts!=EXPECTED_COUNTS: raise ValueError('Unexpected exact entity/join counts')
    if [(r[0],r[1]) for r in history]!=EXPECTED_HISTORY: raise ValueError('Unexpected exact migration history')
    return {'counts':counts,'history':history}

def main():
    report=ROOT/'build/mysql-verification'; report.mkdir(parents=True,exist_ok=True)
    env_file=Path(os.environ.get('COMPOSE_ENV_FILE',str(ROOT/'.env'))).resolve()
    settings={}
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key,value=line.split('=',1); settings[key.strip()]=value.strip()
    for key in ['MYSQL_DATABASE','MYSQL_USER','MYSQL_PASSWORD','MYSQL_ROOT_PASSWORD']:
        if not settings.get(key): raise SystemExit('Missing required setting: '+key)
    if settings['MYSQL_USER']=='root': raise SystemExit('MYSQL_USER must not be root')
    env={**os.environ,**settings,'INTEGRATION_API_PROFILE':'local'}
    compose=['docker','compose','--env-file',str(env_file)]
    if env.get('COMPOSE_PROJECT_NAME'): compose+=['--project-name',env['COMPOSE_PROJECT_NAME']]
    subprocess.run(['docker','info'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    subprocess.run(compose+['config','--quiet'],cwd=ROOT,env=env,check=True)
    subprocess.run(compose+['up','--build','--wait','access-db'],cwd=ROOT,env=env,check=True)
    sql='SELECT version,script,checksum,success FROM flyway_schema_history ORDER BY installed_rank;\n'
    sql+='\n'.join(f"SELECT '{name}',COUNT(*) FROM {name};" for name in EXPECTED_COUNTS)
    def snapshot():
        command=compose+['exec','-T','access-db','sh','-c',
          'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --host=127.0.0.1 --user="$MYSQL_USER" --database="$MYSQL_DATABASE" --batch --skip-column-names']
        return subprocess.run(command,cwd=ROOT,env=env,input=sql,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
    for run in (1,2):
        with (report/f'verify-{run}.log').open('w') as log:
            subprocess.run(['./gradlew','verify'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        state=snapshot(); (report/f'database-{run}.txt').write_text(state)
        parsed=parse_snapshot(state)
        if run==1: first=parsed
        elif parsed!=first: raise SystemExit('Migration checksums or exact entity/join counts changed after restart')
    print('Real MySQL: two read-only local-profile runs passed; exact counts and migration checksums unchanged; volume preserved.')

if __name__=='__main__': main()
