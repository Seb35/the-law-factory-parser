import os, sys
from common import *
#!/usr/bin/env python
# -*- coding: utf-8 -*-

sourcedir = sys.argv[1]
if not sourcedir:
    sys.stderr.write('ERROR: no input directory given\n')
    exit(1)

pagesize = 50
if len(sys.argv) > 2:
    try:
        pagesize = int(sys.argv[2])
    except:
        sys.stderr.write('ERROR: pagesize given as input should be an integer: %s\n' % sys.argv[2])
        exit(1)

dossiers = open_csv(sourcedir, 'dossiers_promulgues.csv')
dossiers = [d for d in dossiers if d.get('Date de promulgation')]
total = len(dossiers)

# Compute dates and length
maxdays = 0
mindate = "9999"
maxdate = ""
for d in dossiers:
    d0 = format_date(d["Date initiale"])
    d1 = format_date(d["Date de promulgation"])
    days = (datize(d1) - datize(d0)).days + 1
    maxdays = max(maxdays, (datize(d1) - datize(d0)).days + 1)
    mindate = min(mindate, d0)
    maxdate = max(maxdate, d1)

dossiers.sort(key=lambda k: format_date(k['Date de promulgation']), reverse=True)

namefile = lambda npage: "dossiers_%s.json" % npage
def save_json_page(tosave, done):
    npage = (done - 1) // pagesize
    data = {"total": total,
            "min_date": mindate,
            "max_date": maxdate,
            "max_days": maxdays,
            "count": len(tosave),
            "page": npage,
            "next_page": None,
            "dossiers": tosave}
    if done < total:
        data["next_page"] = namefile(npage+1)
    print_json(data, os.path.join(sourcedir, namefile(npage)))

done = 0
tosave = []

for d in dossiers:
    proc = open_json(os.path.join(sourcedir, d['id'], 'viz'), 'procedure.json')
    proc["id"] = d["id"]

    for f in ["table_concordance", "objet_du_texte"]:
        if f in proc:
            proc.pop(f)

    tosave.append(proc)
    done += 1
    if done % pagesize == 0:
        print('dossiers.json dumping:', done, 'doslegs')
        save_json_page(tosave, done)
        tosave = []

if tosave:
    save_json_page(tosave, done)
