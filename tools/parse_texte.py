#!/usr/bin/python
# -*- coding=utf-8 -*-
"""Common law parser for AN/Sénat

Run with python parse_texte.py <URL>
Outputs results to stdout

Dependencies :
html5lib, beautifulsoup4"""

import sys, re, copy
from bs4 import BeautifulSoup

from lawfactory_utils.urls import download

try:
    from .sort_articles import bister
    from .common import get_text_id, upcase_accents, real_lower, print_json
except (SystemError, ImportError):
    from sort_articles import bister
    from common import get_text_id, upcase_accents, real_lower, print_json


# inspired by duralex/alinea_parser.py
def word_to_number(word):
    words = {
        'premiere': 1,
        'deuxieme': 2,
        'seconde': 2,
        'troisieme': 3,
        'quatrieme': 4,
        'cinquieme': 5,
        'sixieme': 6,
        'septieme': 7,
        'huitieme': 8,
        'neuvieme': 9,
        'dixieme': 10,
        'onzieme': 11,
        'douzieme': 12,
        'treizieme': 13,
        'quatorzieme': 14,
        'quinzieme': 15,
        'seizieme': 16,
    }

    for i, let in enumerate('abcdefh'):
        words[let] = i + 1

    word = real_lower(word).replace('è', 'e')
    if word in words:
        return str(words[word])


def non_recursive_find_all(node, test):
    """
    if there's a <p> inside a <p>, we don't want to process both
    so we stop at the first top-level <p> we find
    and ignore the children
    """
    if test(node):
        yield node
    elif hasattr(node, 'children'):
        for child in node.children:
            yield from non_recursive_find_all(child, test)


def clean_extra_expose_des_motifs(html):
    """
    the budget related texts have an exposé des motifs per article
    at the depot step, we remove all of them except the last one
    to make the later parsing easier
    """
    before_expose, after_expose = [], []
    last_expose = []
    expose = False
    count = 0
    we_are_inside_an_expose_table = False
    for line in html.split('\n'):
        if '>Exposé des motifs' in line:
            expose = ['']
            count += 1
        # detect end of exposé
        elif line and expose:
            if '<table' in line:
                we_are_inside_an_expose_table = True
            if '</table' in line:
                we_are_inside_an_expose_table = False
            if not we_are_inside_an_expose_table:
                if '"text-align: center">' in line or \
                        '<b>' in line or \
                        '</a>' in line or \
                        '<a name=' in line:
                    last_expose = expose
                    before_expose += after_expose
                    after_expose = []
                    expose = False
        if not expose:
            after_expose.append(line)
        else:
            expose.append(line)
    if count > 3:
        return '\n'.join(before_expose + last_expose + after_expose), True
    return html, False


# Warning changing parenthesis in this regexp has multiple consequences throughout the code
section_titles = "((chap|t)itre|partie|volume|livre|tome|(sous-)?section)"

re_definitif = re.compile(r'<p([^>]*align[=:\s\-]*center"?)?>\(?<(b|strong)>\(?texte d[^f]*finitif\)?</(b|strong)>\)?</p>', re.I)
definitif_before_congres = "<i>(Texte voté par les deux Assemblées du Parlement en termes identiques ; ce projet ne deviendra définitif, conformément à l'article 89 de la Constitution, qu'après avoir été approuvé par référendum ou par le Parlement réuni en Congrès)</i>"
definitif_after_congres = "Le Parlement, réuni en Congrès, a approuvé dans les conditions prévues à l'article 89, alinéa 3, de la Constitution, le projet de loi constitutionnelle dont la teneur suit"

clean_texte_regexps = [
    (re.compile(r'[\n\t\r\s]+'), ' '),
    # (re.compile(r'(<t[rdh][^>]*>) ?<p [^>]*> ?'), r'\1'), # warning: this was to clean tables but the
    # (re.compile(r' ?</p> ?(</t[rdh]>)'), r'\1'),          #          conclusion of report can be in a table too
    (re.compile(r'(>%s\s*[\dIVXLCDM]+(<sup>[eE][rR]?</sup>)?)\s+-\s+([^<]*?)\s*</p>' % section_titles.upper()), r'\1</p><p><b>\6</b></p>'),
    (re.compile(r'(<sup>[eE][rR]?</sup>)(\w+)'), r'\1 \2'), # add missing space, ex: "1<sup>er</sup>A "
    (re.compile(r'(\w)<br/?>(\w)'),  r'\1 \2'), # a <br/> should be transformed as a ' ' only if there's text around it (visual break)
    (re.compile(r'<(em|s)> </(em|s)>'),  r' '), # remove empty tags with only one space inside
]

re_clean_title_legif = re.compile(r"[\s|]*l[eé]gifrance(.gouv.fr)?$", re.I)
clean_legifrance_regexps = [
    (re.compile(r'[\n\t\r\s]+'), ' '),
    (re.compile(r'<a[^>]*>\s*En savoir plus sur ce[^<]*</a>', re.I), ''),
    (re.compile(r'<a/?[^>]*>', re.I), ''),
    (re.compile(r'\s*<br/>\s*', re.I), '</p><p>'),
    (re.compile(r'<div[^>]*class="titreSection[^>]*>\s*(%s\s+[\dIVXLCDM]+e?r?)\s*:\s*([^<]*?)\s*</div>' % section_titles, re.I), r'<p>\1</p><p><b>\5</b></p>'),
    (re.compile(r'<div[^>]*class="titreArt[^>]*>(.*?)\s*</div>', re.I), r'<p><b>\1</b></p>'),
    (re.compile(r'\[Dispositions déclarées non conformes à la Constitution par la décision du Conseil constitutionnel n° \d+-\d+ DC du .{0,30}\]', re.I), "(Censuré)"),
    (re.compile(r'―'), '-'),
    (re.compile(r'([\.\s]+)-([^\s\-]+)'), r'\1 - \2'),
]


# Convert from roman numbers
re_mat_romans = re.compile(r"[IVXCLDM]+", re.I)
romans_map = list(zip(
    (1000,  900, 500, 400 , 100,  90 , 50 ,  40 , 10 ,   9 ,  5 ,  4  ,  1),
    ( 'M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I')
))


def romans(n):
    n = n.upper()
    i = res = 0
    for d, r in romans_map:
        while n[i:i + len(r)] == r:
            res += d
            i += len(r)
    return res


def lower_but_first(text):
    return text[0].upper() + real_lower(text[1:])


re_fullupcase = re.compile(r"^([\W0-9]*)([A-Z%s][\W0-9A-Z%s]*)$" % (upcase_accents, upcase_accents), re.U)


def clean_full_upcase(text):
    mat = re_fullupcase.match(text)
    if mat:
        text = mat.group(1) + lower_but_first(mat.group(2))
    return text

re_clean_premier = re.compile(r'((PREM)?)(1|I)ER?')
re_clean_bister = re.compile(r'([IXV\d]+e?r?)\s+(%s)' % bister, re.I)
re_clean_subsec_space = re.compile(r'^("?[IVX0-9]{1,4}(\s+[a-z]+)?(\s+[A-Z]{1,4})?)\s*([\.°\-]+)\s*([^\s\)])', re.I)
re_clean_subsec_space2 = re.compile(r'^("?[IVX0-9]{1,4})\s*([a-z]*)\s*([A-H]{1,4})([\.°\-])', re.I)
re_clean_punc_space = re.compile(r'([°«»:;,\.!\?\]\)%€&\$])([^\s\)\.,\d"])')
re_clean_spaces = re.compile(r'\s+')
re_clean_coord = re.compile(r'^(<i>)?([\["\(\s]+|pour)*coordination[\]\)\s\.]*(</i>)?', re.I)
# Clean html and special chars
lower_inner_title = lambda x: x.group(1)+lower_but_first(x.group(3))+" "
html_replace = [
    (re.compile(r"−"), "-"),
    (re.compile(r" "), " "),
    (re.compile(r"<!--.*?-->", re.I), ""),
    (re.compile(r"<span[^>]*color: #(0070b9|006fb9)[^>]*>\(\d+\)\s*</span>", re.I), ""), # remove pastilles
    (re.compile(r"<span[^>]*color: white[^>]*>.*?</span>", re.I), ""), # remove invisible text
    (re.compile(r"(<img[^>]*>\s*<br/>\s*)", re.I), ""), # remove <img><br/> before the next regex kills my precious '«'
    (re.compile(r"</?br/?>\s+", re.I), " "),
    (re.compile(r'(«\s+|\s+»)'), '"'),
    (re.compile(r'(«|»|“|”|„|‟|❝|❞|＂|〟|〞|〝)'), '"'),
    (re.compile(r"(’|＇|’|ߴ|՚|ʼ|❛|❜)"), "'"),
    (re.compile(r"(‒|–|—|―|⁓|‑|‐|⁃|⏤)"), "-"),
    (re.compile(r"(</?\w+)[^>]*>"), r"\1>"), # removes html attributes
    (re.compile(r"(</?)em>", re.I), r"\1i>"),
    (re.compile(r"(</?)strong>", re.I), r"\1b>"),
    (re.compile(r"<(![^>]*|/?(p|span))>", re.I), ""),
    (re.compile(r"\s*\n+\s*"), " "),
    (re.compile(r"<[^/>]*></[^>]*>"), ""),
    (re.compile(r"^<b><i>", re.I), "<i><b>"),
    (re.compile(r"</b>(\s*)<b>", re.I), r"\1"),
    (re.compile(r"<a>(\s*)</a>", re.I), r"\1"), # remove bad copy pastes of <a name=..> </a> where the content has been removed
    (re.compile(r"</?sup>", re.I), ""),
    (re.compile(r"^((<[bi]>)*)\((S|AN)[12]\)\s*", re.I), r"\1"),
    (re.compile(r"<s>(.*)</s>", re.I), ""),
    (re.compile(r"^(<b>Article\s*)\d+\s*(?:<s>\s*)+", re.I), r"\1"),
    (re.compile(r"</?s>", re.I), ""),
    (re.compile(r"\s*</?img>\s*", re.I), ""),
    (re.compile(r"œ([A-Z])"), r"OE\1"),
    (re.compile(r"œ\s*", re.I), "oe"),
    (re.compile(r'^((<[^>]*>)*")%s ' % section_titles, re.I), lower_inner_title),
    (re.compile(r' pr..?liminaire', re.I), ' préliminaire'),
    (re.compile(r'<strike>[^<]*</strike>', re.I), ''),
    (re.compile(r'^<a>(\w)', re.I), r"\1"),
    (re.compile(r'^[.…]{5,}\s*(((suppr|conforme).{0,10}?)+)\s*[.…]{5,}\s*$', re.I), r"\1"),  # clean "......Conforme....." to "Conforme"
    (re.compile(r'(\w\s*(?:\</[^>]*>)*\s*)\.{10,}(\s*;)?(</i>)?$', re.I), r"\1\2\3"),  # clean "III. - <i>Conform[e</i>.......]" to "III. - <i>Conform[e</i>]"
    (re_clean_spaces, " ")
]


def clean_html(t):
    for i, (regex, repl) in enumerate(html_replace):
        try:
            t = regex.sub(repl, t)
        except Exception as e:
            print("Crashed while applying regexp", regex, "with replacement", repl, "to", t)
            raise e
    return t.strip()

re_clean_et = re.compile(r'(,|\s+et)\s+', re.I)

def check_section_is_not_a_duplicate(section_id, articles):
    for block in articles:
        assert not (block['type'] == 'section' and block.get('id') == section_id)

re_move_table_guillemets_left = re.compile(r'^(<table[^>]*>(?:<thead[^>]*>.*?</thead>)?(?:<tbody[^>]*>)?<tr[^>]*>)<td[^>]*>\s*"\s*</td>', re.I)
re_move_table_guillemets_right = re.compile(r'<td[^>]*>\s*("\.?)\s*</td>(</tr>(?:</tbody>)?</table>)$', re.I)
re_move_table_guillemets_within = re.compile(r'^(<table[^>]*>(?:<thead[^>]*>.*?</thead>)?(?:<tbody[^>]*>)?<tr[^>]*><td[^>]*>)\s*"\s*([^"]+)\s*("\.?)\s*(</td></tr>(?:</tbody>)?</table>)$', re.I)

def add_to_articles(dic, all_articles):
    # Clean empty articles with only "Supprimé" as text
    if not dic:
        return
    if 'alineas' in dic:
        if len(dic['alineas']) == 1 and dic['alineas']['001'].startswith("(Supprimé)"):
            dic['statut'] = "supprimé"
            dic['alineas'] = {'001': ''}
        elif len(dic['alineas']) == 1 and dic['alineas']['001'].startswith("(Censuré)"):
            dic['statut'] = "supprimé"
        elif dic['statut'].startswith('conforme') and not len(dic['alineas']):
            dic['alineas'] = {'001': '(Non modifié)'}
        # Assume an article is non-modifié if it's empty (but check if it's supprimé)
        # but there's a know side-effect, it may generate non-modifié articles of deleted
        # articles like in the text for article 35 bis:
        #   https://www.senat.fr/rap/l09-567/l09-5671.html
        # or "non-modifié" during the depot for an empty article:
        #   https://www.senat.fr/leg/pjl14-661.html - article 53
        elif not dic['statut'].startswith('suppr') and not len(dic['alineas']):
            dic['alineas'] = {'001': '(Non modifié)'}
        multiples = re_clean_et.sub(',', dic['titre']).split(',')
        if len(multiples) > 1:
            for d in multiples:
                new = copy.deepcopy(dic)
                new['titre'] = d
                all_articles.append(new)
            return
        # Cleanup guillemets around tables
        if '<table' in "".join(dic["alineas"].values()):
            als = {}
            i = 1
            prevguil = None
            prevtabl = None
            for aln in sorted(dic['alineas'].keys()):
                al = dic['alineas'][aln].strip()
                if al in ['"', '".', '";']:
                    if prevtabl:
                        if '"' not in als['%03d' % (i-1)][-2:]:
                            als['%03d' % (i-1)] += al
                        prevtabl = None
                        continue
                    prevguil = al
                    continue
                if '<table' in al:
                    al = re_move_table_guillemets_left.sub(r'"\1', al)
                    al = re_move_table_guillemets_right.sub(r'\2\1', al)
                    al = re_move_table_guillemets_within.sub(r'"\1\2\4\3', al)
                    prevtabl = True
                    if prevguil and not al.startswith('"'):
                        al = '"' + al
                else:
                    prevtabl = None
                prevguil = False
                als['%03d' % i] = al
                i += 1
            dic['alineas'] = als
    all_articles.append(copy.deepcopy(dic))


blank_none = lambda x: x if x else ""
re_cl_html = re.compile(r"<[^>]+>")
re_cl_html_except_tables = re.compile(r"</?[^t/][^>]*>", re.I)
re_fix_missing_table = re.compile(r'(<td>\W*)$', re.I)
cl_html_except_tables = lambda x: re_fix_missing_table.sub(r'\1</td></tr></tbody></table>', re_cl_html_except_tables.sub('', x)).strip().replace('> ', '>').replace(' <', '<').replace('<td><tr>', '<td></td></tr><tr>')
re_cl_par  = re.compile(r"[()\[\]]")
re_cl_uno  = re.compile(r"(premie?r?|unique?)", re.I)
re_cl_sec_uno = re.compile(r"^[Ii1][eE][rR]?")
re_cl_uno_uno = re.compile(r"^1(\s|$)")
re_mat_sec = re.compile(r"(?:<b>)?%s(\s+([^:-]+)e?r?)(?:[:-]\s+(?P<titre>[^<]*))?(?:</b>)?" % section_titles, re.I)
re_cl_sec_simple_num = re.compile(r"(?:<(?P<tag>i|b)>)?(?P<num>[A-Z]|[IVX]{,5})\. - (?P<titre>[^<]+)", re.I) # section name like "B. - XXX" or "<i>IV. - XXX"
re_cl_sec_part = re.compile(r"^(?:<b>)?(?P<num>\w{,11})\s+partie\s*(?::(?P<titre>[^<]*))?(?:</b>)?$", re.I) # partie name like "cinquiéme partie : XXXX"
re_mat_n = re.compile(r"((pr..?)?limin|unique|premier|[IVX\d]+)", re.I)
re_mat_art = re.compile(r"articles?\s*([^(]*)(\([^)]*\))?$", re.I)
re_mat_ppl = re.compile(r"((<b>)?\s*pro.* loi|<h2>\s*pro.* loi\s*</h2>)", re.I)
re_mat_tco = re.compile(r"\s*<b>\s*(ANNEXE[^:]*:\s*|\d+\)\s+)?TEXTES?\s*(ADOPTÉS?\s*PAR|DE)\s*LA\s*COMMISSION.*(</b>\s*$|\(.*\))")
re_mat_exp = re.compile(r"(<b>)?expos[eéÉ]", re.I)
re_mat_end = re.compile(r"((<i>)?Délibéré en|(<i>)?NB[\s:<]+|(<b>)?RAPPORT ANNEX|États législatifs annexés|Fait à .*, le|\s*©|\s*N.?B.?\s*:|(</?i>)*<a>[1*]</a>\s*(</?i>)*\(\)(</?i>)*|<i>\(1\)\s*Nota[\s:]+|La présente loi sera exécutée comme loi de l'Etat|<a>\*</a>\s*(<i>)?1)", re.I)
re_mat_ann = re.compile(r"\s*<b>\s*ANNEXES?[\s<]+")
re_mat_dots = re.compile(r"^(<i>)?([.…_]\s?)+(</i>)?$")
re_mat_st = re.compile(r"(<i>\s?|\(|\[)+(texte)?\s*(conform|non[\s\-]*modif|suppr|nouveau).{0,30}$", re.I)
re_mat_new = re.compile(r"\s*\(\s*nouveau\s*\)\s*", re.I)
re_mat_texte = re.compile(r'\((Adoption du )?texte (modifié|élaboré|voté|d(u|e l))', re.I)
re_mat_single_char = re.compile(r'^\s*[LMN]\s*$')
re_clean_idx_spaces = re.compile(r'^([IVXLCDM0-9]+)\s*\.\s*')
re_clean_art_spaces = re.compile(r'^\s*("?)\s+')
re_clean_art_spaces2 = re.compile(r'\s+\.\s*-\s+')
re_clean_conf = re.compile(r"(?:\(|^([IVX]{1,3}(?: et [IVX]{1,3})?\. - )?)(conforme|non[\s-]*modifi..?)s?(?:\)|$)", re.I)
re_clean_supr = re.compile(r'(?:\(|^)(dispositions?\s*d..?clar..?es?\s*irrecevable.*article 4.*Constitution.*|(maintien de la |Article )?suppr(ession|im..?s?)(\s*(conforme|maintenue|par la commission mixte paritaire))*)\)?[\"\s]*$', re.I)
re_echec_hemi = re.compile(r"L('Assemblée nationale|e Sénat) (a rejeté|n'a pas adopté)[, ]+", re.I)
re_echec_hemi2 = re.compile(r"de loi (a été rejetée?|n'a pas été adoptée?) par l('Assemblée nationale|e Sénat)\.$", re.I)
re_echec_hemi3 = re.compile(r"le Sénat décide qu'il n'y a pas lieu de poursuivre la délibération", re.I)
re_echec_com = re.compile(r"(la commission|elle) .*(effet est d'entraîner le rejet|demande de rejeter|a rejeté|n'a pas adopté|n'a pas élaboré|rejette l'ensemble|ne pas établir|ne pas adopter)[dleau\s]*(projet|proposition|texte)[.\s]", re.I)
re_echec_com2 = re.compile(r"L'ensemble de la proposition de loi est rejeté dans la rédaction issue des travaux de la commission.", re.I)
re_echec_com3 = re.compile(r"la commission (a décidé de déposer une|adopte la) motion tendant à opposer la question préalable", re.I)
re_echec_com4 = re.compile(r"l(a|es) motions?( | .{0,5} )tendant à opposer la question préalable (est|sont) adoptées?", re.I)
re_echec_com5 = re.compile(r"(la|votre) commission a décidé de ne pas adopter [dleau\s]*(projet|proposition|texte)", re.I)
re_echec_com6 = re.compile(r"[dleau\s]*(projet|proposition|texte) est (considéré comme )?rejetée? par la commission", re.I)
re_echec_cmp = re.compile(r" (a conclu à l'échec de ses travaux|(ne|pas) .*parven(u[es]?|ir) à (élaborer )?un texte commun)", re.I)
re_rap_mult = re.compile(r'[\s<>/ai]*N[°\s]*\d+\s*(,|et)\s*[N°\s]*\d+', re.I)
re_src_mult = re.compile(r'^- L(?:A PROPOSITION|E PROJET) DE LOI n°\s*(\d+)\D')
re_clean_mult_1 = re.compile(r'\s*et\s*', re.I)
re_clean_mult_2 = re.compile(r'[^,\d]', re.I)
re_clean_footer_notes = re.compile(r"[\.\s]*\(*\d*\([\d\*]+[\)\d\*\.\s]*$")
re_sep_text = re.compile(r'\s*<b>\s*(article|%s)\s*(I|uniqu|pr..?limina|1|prem)[ier]*\s*</b>\s*$' % section_titles, re.I)
re_stars = re.compile(r'^[\s*_]+$')
re_art_uni = re.compile(r'\s*article\s*unique\s*$', re.I)


def normalize_1(name, one):
    name = name.strip()
    name = re_cl_uno.sub(one, name)
    name = re_cl_sec_uno.sub(one, name)
    name = re_cl_uno_uno.sub(one + r'\1', name)
    return name.strip().strip(" -'")


def normalize_section_title(line, line_soup, has_multiple_expose):
    # transforms "Xeme partie (: <titre>)" to "partie Xeme (: <titre>)"
    m = re_cl_sec_part.match(line)
    if m:
        line = 'partie ' + m.group('num')
        if m.group('titre'):
            line += ' : ' + m.group('titre')
        return line
    # reformats A, B, C, I, II, III sections
    m = re_cl_sec_simple_num.match(line)
    if m:
        # those sections either start with <b> or <i>
        # hack: a "<a name=XXX>" in the html is sufficient
        #       only for depot for pjlf/plfss (multiple expose)
        #       since there are bugs in the AN HTML
        #       ex: alinea III. in article 11 having <a name="XXX">:
        #       http://www.assemblee-nationale.fr/14/ta/ta0208.asp
        if m.group('tag') or ('<a name=' in str(line_soup) and has_multiple_expose):
            # treats A, B, C as sub-sections and I, II, III as sections
            type = 'sous-section' \
                if m.group('tag') == 'b' or re.match(r'[A-H]', m.group('num')) \
                else 'section'
            return '%s %s : %s' % (type, m.group('num'), m.group('titre'))
    return line


def clean_article_name(text):
    # Only keep first line for article name
    # but to do that while keeping the regexes the same
    # we need to add our own marker
    NEW_LINE_MARKER = 'NEW_LINE_MARKER'
    html = str(text)
    html = re.sub(r'<br/?>', NEW_LINE_MARKER, html)
    line = clean_html(html)
    cl_line = re_cl_html.sub("", line).strip()
    cl_line = [l for l in cl_line.split(NEW_LINE_MARKER) if l.strip()][0]

    # If there's a ':', what comes after is not related to the name
    cl_line = cl_line.split(':')[0].strip()

    # simple cleaning of 1er. -> 1er
    cl_line = cl_line.rstrip('.')

    return cl_line

def parse(url, resp=None):
    """
    parse the text of an url, an already cached  to`resp` can be passed to avoid an extra network request
    """
    all_articles = []
    def pr_js(article):
        nonlocal all_articles
        add_to_articles(article, all_articles)

    def save_text(txt):
        if "done" not in txt:
            pr_js(txt)
        txt["done"] = True
        return txt

    if url.endswith('.pdf'):
        print("WARNING: text url is a pdf: %s skipping it..." % url)
        return all_articles
    if 'assemblee-nat.fr' in url:
        print("WARNING: url corresponds to old AN website: %s skipping it..." % url)
        return all_articles


    if url.startswith('http'):
        resp = download(url) if resp is None else resp
        if '/textes/'in url:
            resp.encoding = 'utf-8'
        string = resp.text
    else:
        string = open(url).read()

    string, has_multiple_expose = clean_extra_expose_des_motifs(string)

    if 'legifrance.gouv.fr' in url:
        for reg, res in clean_legifrance_regexps:
            string = reg.sub(res, string)
    else:
        for reg, res in clean_texte_regexps:
            string = reg.sub(res, string)


    definitif = re_definitif.search(string) is not None or 'legifrance.gouv.fr' in url
    soup = BeautifulSoup(string, "html5lib")
    texte = {"type": "texte", "source": url, "definitif": definitif}

    # Generate Senat or AN ID from URL
    if url.startswith('http'):
        if "legifrance.gouv.fr" in url:
            m = re.search(r"cidTexte=(JORFTEXT\d+)(\D|$)", url, re.I)
            texte["id"] = m.group(1)
        elif re.search(r"assemblee-?nationale", url, re.I):
            m = re.search(r"/(\d+)/.+/(ta)?[\w\-]*(\d{4})[\.\-]", url, re.I)
            numero = int(m.group(3))
            texte["id"] = "A" + m.group(1) + "-"
            if m.group(2) is not None:
                texte["id"] += m.group(2)
            texte["id"] += str(numero)
            texte["nosdeputes_id"] = get_text_id(url)
        else:
            m = re.search(r"(ta|l)?s?(\d\d)-(\d{1,3})(rec)?\d?(_mono)?\.", url, re.I)
            if m is None:
                m = re.search(r"/(-)?20(\d+)-\d+/(\d+)(_mono)?.html", url, re.I)
            numero = int(m.group(3))
            texte["id"] = "S" + m.group(2) + "-"
            if m.group(1) is not None:
                texte["id"] += m.group(1)
            texte["id"] += "%03d" % numero
            texte["nossenateurs_id"] = get_text_id(url)

    texte["titre"] = re_clean_title_legif.sub('', soup.title.string.strip()) if soup.title else ""
    texte["expose"] = ""
    expose = False

    # 'read' can be
    #     -1 : the text is not detected yet
    #      0 : read the text
    #      1 : titles lecture
    #      2 : alineas lecture
    read = art_num = ali_num = 0
    section_id = ""
    article = None
    indextext = -1
    curtext = -1
    srclst = []
    section = {"type": "section", "id": ""}

    def should_be_parsed(x):
        """returns True if x can contain useful information"""
        if x.name not in ('p', 'table', 'h2', 'h4'):
            return False
        # hack: we don't want to parse the table containing the conclusion from the senat
        # ex: https://www.senat.fr/leg/tas12-040.html
        if x.name == "table" and re.search("SESSION (EXTRA)?ORDINAIRE DE", str(x)):
            return False
        return True

    for text in non_recursive_find_all(soup, should_be_parsed):
        line = clean_html(str(text))

        # limit h2/h4 matches to PPL headers or Article unique
        if text.name not in ('p', 'table') and not re_mat_ppl.match(line) and 'Article unique' not in line:
            continue

        if re_stars.match(line):
            continue
        if line == "<b>RAPPORT</b>" or line == "Mesdames, Messieurs,":
            read = -1
        if (srclst or indextext != -1) and re_sep_text.match(line):
            curtext += 1
            art_num = 0
        srcl = re_src_mult.search(line)
        cl_line = re_cl_html.sub("", line).strip()
        if srcl and read < 1:
            srclst.append(int(srcl.group(1)))
            continue
        elif re_rap_mult.match(line):
            line = cl_line
            line = re_clean_mult_1.sub(",", line)
            line = re_clean_mult_2.sub("", line)
            cl_line = re_cl_html.sub("", line).strip()
            for n_t in line.split(','):
                indextext += 1
                if int(n_t) == numero:
                    break
        elif re_mat_ppl.match(line) or re_mat_tco.match(line):
            read = 0
            texte = save_text(texte)
        elif re_mat_exp.match(line):
            read = -1 # Deactivate description lecture
            expose = True
        elif read == 0 and definitif_before_congres in line or definitif_after_congres in line:
            texte['definitif'] = True
            if all_articles:
                all_articles[0]['definitif'] = True
            continue
        elif (re_echec_cmp.search(cl_line)
                or re_echec_com.search(cl_line)
                or re_echec_com2.search(cl_line)
                or re_echec_com3.search(cl_line)
                or re_echec_com4.search(cl_line)
                or re_echec_com5.search(cl_line)
                or re_echec_com6.search(cl_line)
                or re_echec_hemi.match(cl_line)
                or re_echec_hemi2.search(cl_line)
                or re_echec_hemi3.search(cl_line)
            ) and 'dont la teneur suit' not in cl_line:
            texte = save_text(texte)
            pr_js({"type": "echec", "texte": cl_line})
            break
        elif read == -1 or (indextext != -1 and curtext != indextext):
            continue

        # crazy edge case: "(Conforme)Article 24 bis A (nouveau)" on one line
        # http://www.assemblee-nationale.fr/13/projets/pl3324.asp
        # simplified, just do the "(Conforme)" case
        if '<i>(Conforme)</i>' in line and re_mat_art.search(line):
            article["statut"] = 'conforme'
            line = line.replace('<i>(Conforme)</i>', '')
            cl_line = cl_line.replace('(Conforme)', '')

        # Identify section zones
        line = normalize_section_title(line, text, has_multiple_expose)
        m = re_mat_sec.match(line)
        if m:
            read = 1 # Activate titles lecture
            section["type_section"] = real_lower(m.group(1))
            section_typ = m.group(1).upper()[0]
            if m.group(3) is not None:
                section_typ += "S"

            if " LIMINAIRE" in line:
                section_num = "L"
            else:
                section_num = re_cl_html.sub('', m.group(5).strip())
                if word_to_number(section_num) is not None:
                    section_num = word_to_number(section_num)
                section_num = normalize_1(section_num, '1')
                section_num = re_clean_bister.sub(lambda m: m.group(1)+" "+real_lower(m.group(2)), section_num)
                section_num = re_mat_new.sub('', section_num).strip()
                m2 = re_mat_romans.match(section_num)
                if m2:
                    rest = section_num.replace(m2.group(0), '')
                    section_num = romans(m2.group(0))
                    if rest: section_num = str(section_num) + rest
            # Get parent section id to build current section id
            section_par = re.sub(r""+section_typ+"[\dL].*$", "", section["id"])
            section["id"] = section_par + section_typ + str(section_num)
            # check_section_is_not_a_duplicate(section["id"])

            titre = blank_none(m.group('titre')).strip()
            if titre:
                texte = save_text(texte)
                section['titre'] = titre
                if article is not None:
                    pr_js(article)
                    article = None
                pr_js(section)
                read = 0
        # Identify titles and new article zones
        elif (not expose and re_mat_end.match(line)) or (read == 2 and re_mat_ann.match(line)):
            break
        elif (re.match(r"(<i>)?<b>", line) or re_art_uni.match(cl_line) or re.match(r"^Articles? ", line)
            ) and not re.search(r">Articles? supprimé", line):

            line = cl_line.strip()
            # Read a new article
            if re_mat_art.match(line):
                if article is not None:
                    texte = save_text(texte)
                    pr_js(article)
                read = 2 # Activate alineas lecture
                expose = False
                art_num += 1
                ali_num = 0
                article = {"type": "article", "order": art_num, "alineas": {}, "statut": "none"}
                if srclst:
                    article["source_text"] = srclst[curtext]
                m = re_mat_art.match(clean_article_name(text))
                article["titre"] = normalize_1(m.group(1), "1er")

                assert article["titre"]  # avoid empty titles
                assert not texte['definitif'] or ' bis' not in article["titre"]  # detect invalid article names

                if m.group(2) is not None:
                    article["statut"] = re_cl_par.sub("", real_lower(m.group(2))).strip()
                if section["id"] != "":
                    article["section"] = section["id"]
            # Read a section's title
            elif read == 1 and line:
                texte = save_text(texte)
                section["titre"] = lower_but_first(line)
                if article is not None:
                    pr_js(article)
                    article = None
                pr_js(section)
                read = 0

        # detect dots, used as hints for later completion
        if read != -1 and len(all_articles) > 0:
            if re_mat_dots.match(line):
                if article is not None:
                    texte = save_text(texte)
                    pr_js(article)
                    article = None
                pr_js({"type": "dots"})
                read = 0
                continue

        # Read articles with alineas
        if read == 2 and not m:
            line = re_clean_coord.sub('', line)
            # if the line was only "Pour coordination", ignore it
            if not line:
                continue
            # Find extra status information
            if ali_num == 0 and re_mat_st.match(line):
                article["statut"] = re_cl_html.sub("", re_cl_par.sub("", real_lower(line)).strip()).strip()
                continue
            if "<table>" in line:
                cl_line = cl_html_except_tables(line)
            line = re_clean_art_spaces2.sub('. - ', re_clean_art_spaces.sub(r'\1', re_clean_idx_spaces.sub(r'\1. ', re_mat_new.sub(" ", cl_line).strip())))
            # Clean low/upcase issues with BIS TER etc.
            line = line.replace("oeUVRE", "OEUVRE")
            line = clean_full_upcase(line)
            line = re_clean_premier.sub(lambda m: (real_lower(m.group(0)) if m.group(1) else "")+m.group(3)+"er", line)
            line = re_clean_bister.sub(lambda m: m.group(1)+" "+real_lower(m.group(2)), line)
            # Clean different versions of same comment.
            line = re_clean_supr.sub('(Supprimé)', line)
            line = re_clean_conf.sub(r'\1(Non modifié)', line)
            line = re_clean_subsec_space.sub(r'\1\4 \5', line)
            line = re_clean_subsec_space2.sub(r'\1 \2 \3\4', line)

            tmp = line
            line = re_clean_punc_space.sub(r'\1 \2', tmp)
            line = re_clean_spaces.sub(' ', line)
            line = re_mat_sec.sub(lambda x: lower_but_first(x.group(1))+x.group(4) if re_mat_n.match(x.group(4)) else x.group(0), line)
            line = re_clean_footer_notes.sub(".", line)
            # Clean comments (Texte du Sénat), (Texte de la Commission), ...
            if ali_num == 0 and re_mat_texte.match(line):
                continue
            line = re_mat_single_char.sub("", line)
            line = line.strip()
            if line:
                ali_num += 1
                article["alineas"]["%03d" % ali_num] = line
        else:
            #metas
            continue

    if article is not None:
        save_text(texte)
        pr_js(article)

    return all_articles


if __name__ == '__main__':
    if '--test' not in sys.argv:
        print_json(parse(sys.argv[1]))
    else:
        def assert_eq(x, y):
            if x != y:
                print(repr(x), "!=", repr(y))
                raise Exception()
        # keep the dots
        assert_eq(clean_html('<i>....................</i>'), '<i>....................</i>')
        # but remove them for status
        assert_eq(clean_html('...........Conforme.........'), 'Conforme')
        assert_eq(clean_html('...……......……..Conforme....……...…….'), 'Conforme')
        # even with spaces
        assert_eq(clean_html('...........  Conforme   .........'), 'Conforme')
        # or for alineas
        assert_eq(clean_html('II. - <i>Conforme</i>...............'), 'II. - <i>Conforme</i>')
        # even midway alineas
        assert_eq(clean_html('II. - <i>Conforme</i>............... ;'), 'II. - <i>Conforme</i> ;')

        # clean empty <a> tags
        assert_eq(clean_html('<b><a name="P302_55065"></a>ANNEXE N° 1 :<a name="P302_55081"><br/> </a>TEXTE ADOPTÉ PAR LA COMMISSION</b>'), '<b>ANNEXE N° 1 : TEXTE ADOPTÉ PAR LA COMMISSION</b>')

        # clean status
        assert_eq(re_clean_conf.sub(r'\1(Non modifié)', 'IV. - Non modifié'), 'IV. - (Non modifié)')
        assert_eq(re_clean_conf.sub(r'\1(Non modifié)', 'III et IV. - Non modifié'), 'III et IV. - (Non modifié)')

        assert_eq(normalize_1('1', '1er'), '1er')
        assert_eq(normalize_1('17', '1er'), '17')
        assert_eq(normalize_1('1 bis', '1er'), '1er bis')

        assert_eq(clean_article_name('Article 5.'), 'Article 5')

        assert_eq(re_mat_sec.match('<b>Titre Ier - Cuire un oeuf</b>').group('titre').strip(), 'Cuire un oeuf')
