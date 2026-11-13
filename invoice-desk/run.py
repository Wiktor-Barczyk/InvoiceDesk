"""Native launcher: Python 3.11, .env loaded without shell interpolation."""
import argparse
import os
from pathlib import Path
import runpy
import sys


def main():
    from dotenv import load_dotenv
    root=Path(__file__).resolve().parent
    os.chdir(root)
    load_dotenv(root/'.env',override=True,interpolate=False)
    parser=argparse.ArgumentParser()
    parser.add_argument('command',nargs='?',default='web',choices=['web','migrate','enable-mcp','revoke-mcp'])
    args=parser.parse_args()
    if args.command=='web':
        import uvicorn
        uvicorn.run('app.main:create_app',factory=True,host='127.0.0.1',port=8001,access_log=False)
    else:
        sys.argv=['app.manage',args.command]
        runpy.run_module('app.manage',run_name='__main__')


if __name__=='__main__':main()
