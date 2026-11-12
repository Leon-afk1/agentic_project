import json, time, urllib.request, sys
sys.stdout.reconfigure(encoding="utf-8")
URL="https://www.datagrandest.fr/geonetwork/srv/api/search/records/_search"
UA="dge-catalog-explorer/0.1 (personal research project; read-only)"
QS=["pistes cyclables Strasbourg","mobilité douce","zones inondables Bas-Rhin","établissements scolaires Strasbourg",
"qualité de l'air Nancy","bornes de recharge véhicules électriques","occupation du sol Grand Est","ligne Maginot",
"Quelles données sur les pistes cyclables à Strasbourg","où sont les écoles à Strasbourg","prix de l'immobilier à Reims","vélo Strasbourg","aménagements cyclables Eurométropole"]
out=[]
for q in QS:
    body={"from":0,"size":5,"sort":[{"_score":"desc"}],"track_total_hits":True,
     "query":{"bool":{"must":[{"query_string":{"query":q,"default_operator":"AND","fields":["resourceTitleObject.langfre^5","tag.langfre^4","resourceAbstractObject.langfre^3","lineageObject.langfre^2","any.langfre","uuid"]}}],
     "must_not":[{"query_string":{"query":"resourceType:featureCatalog AND !resourceType:dataset AND !cl_level.key:dataset"}}],"filter":[{"terms":{"isTemplate":["n"]}}]}},
     "_source":["uuid","resourceTitleObject","OrgForResourceObject"]}
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":UA})
    t=time.time(); d=json.loads(urllib.request.urlopen(req,timeout=60).read()); dt=time.time()-t
    hits=[(h["_source"]["resourceTitleObject"]["default"][:85],(h["_source"].get("OrgForResourceObject") or {}).get("default","") if isinstance(h["_source"].get("OrgForResourceObject"),dict) else "") for h in d["hits"]["hits"]]
    print(f"\n## {q!r} -> {d['hits']['total']['value']} results ({dt*1000:.0f} ms)")
    for t_,o in hits: print("   -",t_,"|",str(o)[:40])
    out.append({"q":q,"total":d["hits"]["total"]["value"],"ms":round(dt*1000),"top5":hits}); time.sleep(1.5)
json.dump(out,open("baseline.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
