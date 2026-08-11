from __future__ import annotations
import argparse,json,sys
from pathlib import Path
PROJECT_ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(PROJECT_ROOT))
from linehelper.catalogs.search import CatalogSearch
from linehelper.catalogs.store import CatalogStore
def main():
 ap=argparse.ArgumentParser(); ap.add_argument("query"); ap.add_argument("--exact",action="store_true"); ap.add_argument("--assembly",action="store_true"); ap.add_argument("--diagnostic",action="store_true"); ap.add_argument("--limit",type=int,default=20); ap.add_argument("--db",type=Path,default=PROJECT_ROOT/"data/catalogs/catalog_store.db"); a=ap.parse_args()
 s=CatalogSearch(CatalogStore(a.db)); results=s.find_assembly_parts(a.query) if a.assembly else (s.find_part_by_number(a.query) if a.exact else s.search_parts(a.query,a.limit))
 if a.diagnostic:
  print(f"query: {a.query}")
  for rank,result in enumerate(results,1): print(f"{rank}. {result.part_number} | {result.part_name or '-'} | score={result.search_score}")
 else: print(json.dumps([x.to_dict() for x in results],ensure_ascii=False,indent=2))
if __name__=="__main__": main()
