import json, glob, re, collections, sys, html
sys.stdout.reconfigure(encoding="utf-8")
recs=[]
for f in sorted(glob.glob("pages/p*.json")):
    recs+= [h["_source"] for h in json.load(open(f,encoding="utf-8"))["hits"]["hits"]]
print("records",len(recs),"unique uuid",len({r["uuid"] for r in recs}))
def txt(o):
    if not o: return ""
    s=o.get("default","") if isinstance(o,dict) else str(o)
    s=re.sub(r"<[^>]+>"," ",s); s=html.unescape(s)
    return re.sub(r"\s+"," ",s).strip()
def aslist(x): return x if isinstance(x,list) else ([] if x is None else [x])
TECH=[("underscore",re.compile(r"\w_\w")),("file_ext",re.compile(r"\.(shp|csv|zip|xlsx?|tif|pdf|json|gpkg)\b",re.I)),
      ("no_space",re.compile(r"^\S+$")),("version_tag",re.compile(r"\b(v\d+|final|copie|test|tmp)\b",re.I)),
      ("short_lt10",re.compile(r"^.{0,9}$"))]
def flags(r):
    t=txt(r.get("resourceTitleObject")); a=txt(r.get("resourceAbstractObject"))
    tags=[txt(x) for x in aslist(r.get("tag")) if txt(x)]
    dates=aslist(r.get("resourceDate"))
    dtypes={d.get("type") for d in dates if isinstance(d,dict)}
    tech=[n for n,rx in TECH if rx.search(t)]
    return {
     "no_title":not t,
     "no_abstract":not a,
     "abstract_lt50":len(a)<50,
     "abstract_lt100":len(a)<100,
     "abstract_eq_title":bool(a) and a.lower()==t.lower(),
     "no_keywords":not tags,
     "kw_le2":len(tags)<=2,
     "no_topic":not r.get("cl_topic"),
     "tech_title":bool(tech),
     "no_geom":not r.get("geom"),
     "no_resource_date":not dates,
     "no_rev_or_pub_date":not (dtypes & {"revision","publication"}),
     "no_revision_date":"revision" not in dtypes,
     "no_temporal_extent":not (r.get("resourceTemporalDateRange") or r.get("resourceTemporalExtentDateRange")),
     "no_license":not r.get("licenseObject") and not r.get("MD_LegalConstraintsOtherConstraintsObject"),
     "no_link":not r.get("linkUrl"),
     "no_update_freq":not r.get("cl_maintenanceAndUpdateFrequency"),
     "no_lineage":not txt(r.get("lineageObject")),
     "html_in_abstract":bool(re.search(r"<(p|div|span|br|a)\b",(r.get("resourceAbstractObject") or {}).get("default","") if isinstance(r.get("resourceAbstractObject"),dict) else "")),
    }, tech, t, a
rows=[(r,)+flags(r) for r in recs]
def report(name,rows):
    n=len(rows); print(f"\n### {name} (n={n})")
    c=collections.Counter()
    for _,f,_,_,_ in rows:
        for k,v in f.items(): c[k]+=bool(v)
    for k in rows[0][1]: print(f"  {k:22s} {c[k]:5d}  {100*c[k]/n:5.1f}%")
    # at least one core defect
    core=["no_abstract","abstract_lt50","no_keywords","no_topic","tech_title","no_geom","no_resource_date"]
    k=sum(any(f[x] for x in core) for _,f,_,_,_ in rows); print(f"  >=1 core defect        {k:5d}  {100*k/n:5.1f}%")
    core2=["abstract_lt50","no_keywords","no_topic"]
    k=sum(any(f[x] for x in core2) for _,f,_,_,_ in rows); print(f"  >=1 of (abs<50,noKw,noTopic) {k:5d}  {100*k/n:5.1f}%")
report("ALL",rows)
DS={"dataset","series","nonGeographicDataset"}
ds=[x for x in rows if set(aslist(x[0].get("resourceType")))&DS]
report("DATASETS (dataset/series/nonGeographicDataset)",ds)
loc=[x for x in ds if str(x[0].get("isHarvested")).lower() not in("true","1")]
report("DATASETS not harvested",loc)
har=[x for x in ds if str(x[0].get("isHarvested")).lower() in("true","1")]
report("DATASETS harvested",har)
tc=collections.Counter(n for x in ds for n in x[2]); print("\ntech title reasons (datasets):",tc)
import random; random.seed(0)
for n,_ in TECH:
    s=[x[3] for x in ds if n in x[2]]; print("\n--",n,len(s)); [print("   ",t[:90]) for t in random.sample(s,min(8,len(s)))]
print("\n-- short abstracts sample"); s=[(x[3],x[4]) for x in ds if 0<len(x[4])<50]
[print("   ",t[:60],"||",a) for t,a in random.sample(s,min(12,len(s)))]
al=sorted(len(x[4]) for x in ds); print("\nabstract len median",al[len(al)//2],"p10",al[len(al)//10],"p90",al[9*len(al)//10])
kl=sorted(len([t for t in aslist(x[0].get("tag"))]) for x in ds); print("kw count median",kl[len(kl)//2])
tt=collections.Counter(x[3].lower() for x in ds); d=sum(v for v in tt.values() if v>1); print("datasets sharing an identical title:",d, "distinct dup titles",sum(1 for v in tt.values() if v>1)); print(tt.most_common(6))
qs=[x[0].get("qualityScore") for x in ds if isinstance(x[0].get("qualityScore"),(int,float))]; qs.sort(); print("qualityScore n",len(qs),"median",qs[len(qs)//2] if qs else None)
lp=collections.Counter(p for x in ds for p in set(aslist(x[0].get("linkProtocol")))); print("\nlink protocols (datasets having >=1):",lp.most_common(20))
src=json.load(open("sources.json",encoding="utf-8")); nm={s["uuid"]:s["name"] for s in src}
by=collections.defaultdict(list)
for x in ds: by[nm.get(x[0].get("sourceCatalogue"),x[0].get("sourceCatalogue"))].append(x)
print("\nper source (datasets): n, %abs<50, %noKw, %noTopic, %tech_title, %noGeom")
for k,v in sorted(by.items(),key=lambda kv:-len(kv[1]))[:20]:
    n=len(v); p=lambda key: f"{100*sum(f[key] for _,f,_,_,_ in v)/n:4.0f}"
    print(f"  {k[:42]:42s} {n:5d} {p('abstract_lt50')} {p('no_keywords')} {p('no_topic')} {p('tech_title')} {p('no_geom')}")
json.dump([{"uuid":x[0]["uuid"],"title":x[3],"abstract_len":len(x[4]),**{k:bool(v) for k,v in x[1].items()}} for x in ds],open("quality_rows.json","w",encoding="utf-8"))
