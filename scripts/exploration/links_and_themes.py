import json, glob, collections, sys
from urllib.parse import urlparse
sys.stdout.reconfigure(encoding="utf-8")
recs=[]
for f in sorted(glob.glob("pages/p*.json")): recs+=[h["_source"] for h in json.load(open(f,encoding="utf-8"))["hits"]["hits"]]
al=lambda x: x if isinstance(x,list) else ([] if x is None else [x])
DS={"dataset","series","nonGeographicDataset"}
ds=[r for r in recs if set(al(r.get("resourceType")))&DS]
hosts=collections.Counter();
for r in ds:
    for h in {urlparse(u).netloc.lower() for u in al(r.get("linkUrl")) if isinstance(u,str)}: hosts[h]+=1
print("datasets",len(ds)); print("top link hosts (datasets with >=1 link on host):")
for h,c in hosts.most_common(25): print("  ",c,h)
def has(r,pred): return any(pred(p or "",u or "") for p,u in zip(al(r.get("linkProtocol")),al(r.get("linkUrl"))))
wfs=sum(has(r,lambda p,u:"WFS" in p.upper()) for r in ds)
wfs_dge=sum(has(r,lambda p,u:"WFS" in p.upper() and "datagrandest.fr" in u) for r in ds)
csvjson=sum(has(r,lambda p,u:any(k in (p+u).lower() for k in("csv","json"))) for r in ds)
dl=sum(has(r,lambda p,u:"DOWNLOAD" in p.upper()) for r in ds)
print("with WFS link",wfs,"| WFS on datagrandest.fr",wfs_dge,"| csv/json link",csvjson,"| any download link",dl)
prev=sum(has(r,lambda p,u:"WFS" in p.upper() or any(k in (p+u).lower() for k in("csv","json"))) for r in ds); print("previewable in principle (WFS or csv/json):",prev, f"{100*prev/len(ds):.1f}%")
keys=collections.Counter(k for r in ds for k in r if k.startswith("th_") and not k.endswith("Number") and not k.endswith("_tree"))
print("thesauri:",keys.most_common(15))
insp=sum(str(r.get("hasInspireTheme")).lower()=="true" for r in ds); print("hasInspireTheme",insp)
ntop=collections.Counter(len(al(r.get("cl_topic"))) for r in ds); print("topics per dataset:",sorted(ntop.items()))
yrs=collections.Counter((r.get("changeDate") or "")[:4] for r in ds); print("changeDate year:",sorted(yrs.items()))
