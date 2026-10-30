"""Explicit evaluation CLI; never runs on application startup."""
import argparse, asyncio, json, tomllib
from pathlib import Path
from .config import Settings
from .routing import ModelRegistry
from .evaluation_v069 import protocol_probe, semantic_evaluation

def main():
    parser=argparse.ArgumentParser(description='AlienApe v0.6.9: explicitly run a local evaluation.')
    parser.add_argument('--config',required=True,help='Path to an existing AlienApe config; legacy plaintext keys are rejected.')
    parser.add_argument('--output',required=True,help='New JSON results file; existing files are never overwritten.')
    sub=parser.add_subparsers(dest='mode',required=True)
    probe=sub.add_parser('protocol');probe.add_argument('--model',action='append',required=True,help='Explicit model profile ID; repeat to compare.')
    semantic=sub.add_parser('semantic');semantic.add_argument('--fixture',required=True)
    args=parser.parse_args(); config=Path(args.config); output=Path(args.output)
    if not config.is_file():parser.error('Config file does not exist.')
    if output.exists():parser.error('Output file already exists.')
    raw=tomllib.loads(config.read_text(encoding='utf-8-sig'))
    if 'api_key' in raw.get('model',{}) or any('api_key' in m for m in raw.get('models',[])):parser.error('Legacy plaintext keys are not supported; migrate separately.')
    settings=Settings.load(config)
    if args.mode=='protocol':result=asyncio.run(protocol_probe(ModelRegistry.from_settings(settings),settings,args.model))
    else:
        fixture=json.loads(Path(args.fixture).read_text(encoding='utf-8-sig'))
        result=asyncio.run(semantic_evaluation(settings.semantic_resolver,fixture))
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print('Results saved:', output)
if __name__=='__main__':main()
