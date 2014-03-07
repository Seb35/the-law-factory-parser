#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re, csv, os, sys
from difflib import ndiff, SequenceMatcher
try:
    import json
except:
    import simplejson as json

sourcedir = sys.argv[1]
if not sourcedir:
    sys.stderr.write('Error, no input directory given')
    exit(1)
try:
    with open(os.path.join(sourcedir, 'procedure.json'), "r") as procedure:
        procedure = json.load(procedure)
except:
    sys.stderr.write('Error: could not find or load procedure.json in directory %s' % sourcedir)
    exit(1)

def getParentFolder(root, f):
    abs = os.path.abspath(os.path.join(root, f))
    return os.path.basename(os.path.abspath(os.path.join(abs, os.pardir)))

def unifyStatus(status):
    status = status.encode('utf-8')
    status = status.lstrip().rstrip('s. ')
    if status.endswith('constitution') or status.startswith('sup'):
        return "sup"
    if status.startswith("nouveau"):
        return "new"
    return "none"

def create_step(step_id, article):
    s = {}
    s['id_step'] = step_id
    if article.get('statut'):
        s['status'] = unifyStatus(article['statut'])
    else:
        s['status'] = 'none'
    s['text'] = []
    for key in sorted(article['alineas'].keys()):
        if article['alineas'][key] != '':
            s['text'].append(article['alineas'][key])
    s['order'] = article['order']
    return s

title = procedure.get("long_title", "Missing title").replace(procedure.get("short_title", "").lower(), procedure.get("short_title", ""))
title = title[0].upper() + title[1:]
out = {'law_title': title, 'articles': {}, 'short_title': procedure.get("short_title", "")}

step_id = ''
old_step_id = ''
tmpsteps = procedure['steps']
# Handle reorder of repeated depots (typically a few PPL from Senat similar to a PJL added to its dossier)
first = tmpsteps.pop(0)
firsts_sen = []
steps = []
for step in tmpsteps:
    if not 'step' in step or step['step'] != 'depot':
        steps.append(step)
    else:
        firsts_sen.insert(0, step)
depots = 1
depots_sen = len(firsts_sen)
if "/leg/pjl" in first['source_url'] or "/leg/ppl" in first['source_url']:
    depots_sen += 1
steps.insert(0, first)
if depots_sen > 1:
    for f in firsts_sen:
        steps.insert(0, f)
        depots += 1

nsteps = len(["" for a in steps if a['stage'] not in ['promulgation', u'constitutionnalité']]) - 1
for nstep, step in enumerate(steps):
    last_step = (nstep == nsteps and
      step['institution'] == 'assemblee' and
      step.get('step', '') == 'hemicycle' and
      not step['stage'].endswith('lecture'))

    if not 'resulting_text_directory' in step:
        if step['stage'] not in [u"promulgation", u"constitutionnalité"]:
            sys.stderr.write("WARNING no directory found for step %s\n" % step['stage'])
        continue
    try:
        path = os.path.join(sourcedir, step['resulting_text_directory'])
        step_id = "%02d%s" % (nstep, step['directory'][2:])

        for root, dirs, files in os.walk(path):
            articleFiles = [os.path.abspath(os.path.join(root,f)) for f in files if re.search(r'^A.*', getParentFolder(root, f)) and re.search(r'^.*?json', f)]
            if last_step and len(articleFiles) != 1:
                print >> sys.stderr, "INFO: skipping final text adopté renuméroté AN", step['directory'].encode('utf-8')
                break
            if len(articleFiles) > 0:
                for articleFile in articleFiles:
                    with open(articleFile,"r") as article:
                         article = json.load(article)

                    id = article['titre'].replace(' ', '_')
                    if out['articles'].get(id):
                        s = create_step(step_id, article)
                        txt = " ".join(s['text'])
                        oldtext = None
                        for st in out['articles'][id]['steps']:
                            if st['id_step'] == old_step_id:
                                oldtext = st['text']
                                break
                        if not oldtext or nstep < depots:
                            s['status'] = 'new' if nstep >= depots else 'none'
                            s['diff'] = 'add'
                            s['n_diff'] = 1
                        elif s['status'] == "sup":
                            s['diff'] = 'rem'
                            s['n_diff'] = 0
                        else:
                            oldtxt = " ".join(oldtext)
                            s['status'] = 'none'
                            if txt == oldtxt:
                                s['diff'] = 'none'
                                s['n_diff'] = 0
                            else:
                                compare = list(ndiff(s['text'], oldtext))
                                mods = {'+': 0, '-': 0}
                                for line in compare:
                                    mod = line[0]
                                    if mod not in mods:
                                        mods[mod] = 0
                                    mods[mod] += 1
                                if mods['+'] > mods['-']:
                                    s['diff'] = 'add'
                                elif mods['+'] < mods['-']:
                                    s['diff'] = 'rem'
                                elif mods['+'] * mods['-']:
                                    s['diff'] = 'both'
                                else:
                                    s['diff'] = 'none'
                                a = SequenceMatcher(None, oldtxt, txt).ratio()
                                b = SequenceMatcher(None, txt, oldtxt).ratio()
                                s['n_diff'] = 1 - (a + b)/2
                    else:
                        out['articles'][id] = {}
                        out['articles'][id]['id'] = id
                        out['articles'][id]['titre'] = article['titre']
                        if article.get('section'):
                            out['articles'][id]['section'] = article['section']
                        else:
                            out['articles'][id]['section'] = 'none'
                        out['articles'][id]['steps'] = []
                        s = create_step(step_id, article)
                        s['n_diff'] = 1
                        s['diff'] = 'add'
                        if nstep >= depots:
                            s['status'] = 'new'
                        else:
                            s['status'] = 'depot'
                        txt = " ".join(s['text'])
                    if s['status'] == 'sup':
                        s['length'] = 50
                        s['n_diff'] = 0
                    else:
                        s['length'] = len(txt)
                    out['articles'][id]['steps'].append(s)
        if 'step' in step and (step['step'] != 'depot' or nstep == 0):
            old_step_id = step_id

    except Exception as e:
        sys.stderr.write("ERROR parsing step %s:\n%s: %s\n" % (step, type(e), e))
        exit(1)

print json.dumps(out, ensure_ascii=False).encode('utf8')
