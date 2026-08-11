from __future__ import annotations
import argparse, json, sys
from pathlib import Path
PROJECT_ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(PROJECT_ROOT))
from linehelper.catalogs.indexing import rebuild_search_index
from linehelper.catalogs.khs_etl_parser import KHSETLCatalogParser
from linehelper.catalogs.store import CatalogStore
from linehelper.catalogs.validation import validate_staging

def main() -> int:
    ap=argparse.ArgumentParser(description="Import one KHS ETL spare-parts PDF through staging")
    ap.add_argument("--file",type=Path,required=True); ap.add_argument("--site",required=True)
    ap.add_argument("--staging-only",action="store_true"); ap.add_argument("--commit",action="store_true"); ap.add_argument("--backfill-reference-pages",action="store_true")
    ap.add_argument("--db",type=Path,default=PROJECT_ROOT/"data/catalogs/catalog_store.db")
    args=ap.parse_args()
    if sum((args.staging_only, args.commit, args.backfill_reference_pages)) > 1: ap.error("choose one action")
    if not args.file.is_file(): ap.error(f"PDF does not exist: {args.file}")
    staging=KHSETLCatalogParser().parse(args.file,args.site); report=validate_staging(staging)
    import_id=staging["catalog"]["source_checksum"][:16]; out=PROJECT_ROOT/"data/catalogs/staging"/import_id; out.mkdir(parents=True,exist_ok=True)
    for name,value in (("catalog",staging["catalog"]),("assemblies",staging["assemblies"]),("parts",staging["parts"]),("bom_items",staging["bom_items"]),("pages",staging["pages"]),("validation_report",report)):
        (out/f"{name}.json").write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"staging: {out}"); print(json.dumps(report,ensure_ascii=False,indent=2))
    if args.backfill_reference_pages:
        updated = CatalogStore(args.db).backfill_reference_pages(staging)
        print(f"reference pages backfilled: {updated}")
    elif args.commit:
        if report["quality_gate"] != "pass": print("quality gate failed; DB not modified",file=sys.stderr); return 2
        store=CatalogStore(args.db)
        try: catalog_id=store.import_staging(staging)
        except ValueError as e: print(str(e),file=sys.stderr); return 3
        rebuild_search_index(store); print(f"committed catalog_id={catalog_id} db={args.db}")
    return 0
if __name__=="__main__": raise SystemExit(main())
