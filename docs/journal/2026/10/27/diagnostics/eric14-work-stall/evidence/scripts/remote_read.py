import subprocess,base64,zlib,sys
script=sys.stdin.read()
b=base64.b64encode(zlib.compress(script.encode())).decode()
cmd='py -3 -I -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\''+b+'\')))"'
r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-i','C:/Users/djlov/.ssh/id_ed25519','-p','1004','ara-ai@100.64.0.14',cmd],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=75)
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
print(r.stdout);print(r.stderr);print('exit',r.returncode)
