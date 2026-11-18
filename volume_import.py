"""Copy an offline encrypted named volume into a NEW owned volume; never adopt/mutate it."""
import json
from pathlib import Path
import re
import time


def users(runtime,volume):
    ids=runtime.call('ps','-aq','--filter','volume='+volume).stdout.decode().split()
    if not ids: return []
    records=json.loads(runtime.call('container','inspect',*ids).stdout)
    return [row for row in records if any(m.get('Type')=='volume' and m.get('Name')==volume for m in row.get('Mounts',[]))]


def offline(runtime,volume):
    from cli import require
    for row in users(runtime,volume):
        require(not row['State']['Running'] and not row['State'].get('Paused'), 'Source volume is in use; stop its containers before import')


def receipt_path(config_path,volume):
    return Path(config_path).parent/(volume+'.import.json')


def check_import(runtime,info,config_path):
    from cli import LABEL,require,private_read
    labels=info.get('Labels') or info.get('labels') or {}
    if LABEL+'.import-source' not in labels: return
    receipt=json.loads(private_read(receipt_path(config_path,runtime.volume)))
    require(receipt.get('status')=='complete' and receipt.get('target')==runtime.volume and receipt.get('source')==labels[LABEL+'.import-source'] and receipt.get('createdAt')==info.get('CreatedAt'), 'Incomplete or mismatched encrypted import; refusing to mount')


def import_volume(runtime,source,config_path):
    from cli import LABEL,require,save_new,source_hash
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',source) is not None and source!=runtime.volume,'Invalid or identical source volume')
    require(runtime.inspect('container',runtime.container) is None,'Destination container already exists')
    require(runtime.inspect('volume',runtime.volume) is None,'Destination volume already exists; never overwrite it')
    original=runtime.inspect('volume',source)
    require(original is not None,'Source volume is missing')
    offline(runtime,source)
    receipt=receipt_path(config_path,runtime.volume)
    require(not receipt.exists() and not receipt.is_symlink(),'Import receipt already exists')
    image=runtime.inspect('image',runtime.config['image'])
    require(image is not None,'Build the selected image first')
    if runtime.config['image']=='localhost/agent-container:dev':
        require(image.get('Labels',{}).get(LABEL+'.source')==source_hash(),'Selected image does not match this source')
    runtime.call('volume','create','--label',runtime.label,'--label',LABEL+'.import-source='+source,runtime.volume)
    target=runtime.inspect('volume',runtime.volume); runtime.owned(target)
    record={'version':1,'status':'incomplete','source':source,'sourceCreatedAt':original.get('CreatedAt'),'target':runtime.volume,'createdAt':target.get('CreatedAt')}
    save_new(receipt,(json.dumps(record)+'\n').encode())
    # The copier sees ciphertext only. Root is confined to the existing rootless
    # mapping and only has the metadata-preservation capabilities it needs.
    result=runtime.call('run','--rm','--init','--name',runtime.container+'-import','--label',runtime.label,'--label',LABEL+'.purpose=encrypted-import','--http-proxy=false','--network','none','--userns=keep-id:uid=1000,gid=1000','--user','root',
        '--cap-drop','ALL','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--cap-add','MKNOD',
        '--security-opt','no-new-privileges','--security-opt','label=disable',
        '--read-only','--entrypoint','/usr/local/bin/cipher-copy',
        '-v',source+':/source:ro','-v',runtime.volume+':/destination',runtime.config['image'],timeout=3600)
    verification=json.loads(result.stdout)
    require(isinstance(verification,dict) and set(verification)=={'digest','entries','bytes'} and re.fullmatch(r'[a-f0-9]{64}',verification['digest']) is not None,'Invalid copy verification')
    offline(runtime,source)
    current=runtime.inspect('volume',source)
    require(current is not None and current.get('CreatedAt')==original.get('CreatedAt'),'Source volume identity changed')
    record.update(status='complete',verification=verification,completedAt=int(time.time()))
    temporary=receipt.with_suffix('.complete')
    save_new(temporary,(json.dumps(record,indent=2)+'\n').encode())
    temporary.replace(receipt)
    print('Encrypted copy verified. Original volume retained. Unlock with the existing passphrase; do not initialize a new store.')
    return record
