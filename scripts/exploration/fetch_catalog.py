import json, time, urllib.request, os, sys
URL="https://www.datagrandest.fr/geonetwork/srv/api/search/records/_search"
UA="dge-catalog-explorer/0.1 (personal research project; read-only)"
INC=["uuid","resourceTitleObject","resourceAbstractObject","tag","cl_topic","resourceType","format","linkProtocol","linkUrl",
"geom","location","resourceTemporalDateRange","resourceTemporalExtentDateRange","resourceDate","changeDate","createDate","dateStamp",
"publicationDateForResource","revisionDateForResource","creationDateForResource","OrgForResourceObject","sourceCatalogue","isHarvested",
"licenseObject","MD_LegalConstraintsOtherConstraintsObject","qualityScore","hasInspireTheme","th_*","lineageObject",
"cl_maintenanceAndUpdateFrequency","documentStandard","featureTypes","mainLanguage","resourceLanguage","popularity","hasOverview","indexingErrorMsg"]
size=500; frm=0; total=None
while total is None or frm<total:
    fn=f"pages/p{frm:05d}.json"
    if not os.path.exists(fn):
        body={"from":frm,"size":size,"track_total_hits":True,"sort":[{"uuid":"asc"}],
              "query":{"query_string":{"query":"+isTemplate:n"}},"_source":{"includes":INC}}
        req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":UA})
        t=time.time(); raw=urllib.request.urlopen(req,timeout=90).read()
        open(fn,"wb").write(raw); print(fn,len(raw),round(time.time()-t,2),flush=True); time.sleep(2)
    d=json.load(open(fn,encoding="utf-8")); total=d["hits"]["total"]["value"]
    if not d["hits"]["hits"]: break
    frm+=size
print("total",total)
