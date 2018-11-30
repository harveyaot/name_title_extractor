import spacy
import re
import json
import redis
from spacy.matcher import PhraseMatcher, Matcher
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
conn = redis.StrictRedis()


nlp = spacy.load('en_core_web_sm')
title_set = frozenset(map(lambda x:x.strip('\t\n'), 
                    [x for x in open("./data/title_list_v2.tsv").readlines()
                    if not x.startswith("#")]))

pat_beg = re.compile('^[\s“”]+')
pat_end = re.compile('[\s“”]+$')

def post_process(title):
    if title is not None:
        title = pat_beg.sub("",title)
        title = pat_end.sub("",title)
    return title

def debug_show_tokens_in_span(span):
    for token in span:
        print(token.text, '#', token.tag_, '#', token.pos_)
  
def remove_det_punt_for_title_span(doc, span, start, end):
    # only deal with the sentence is starts with name:
    #e.x: Yana Pechenik, a physician assistant at MyBotoxLA,
    if span is not None:
        i, j = span[0].i, span[-1].i
        while(i<j):
            if doc[i].is_punct or doc[i].tag_ == "DT":
                i += 1
            elif doc[j].is_punct:
                j -= 1
            else:
                break
        if j > i :
            span = doc[i:j+1]
    return span
      
def method_depend_parsing(doc, start,  end, debug=False):
    # the root is the last name usually
    root = doc[end-1]
    title_span =  None
    # detect sub-chunks to describe the name:
    seqs = list(root.subtree)
    s = seqs[0].i
    e = seqs[-1].i
    # only deal with this situation so far.
    # sub-tree is continuous and 
    if e-s == len(seqs)-1 and s == start:
        title_span = doc[end:e+1]
        # Guantee the insetation here
        if debug:
            debug_show_tokens_in_span(title_span)
        # only think on the insertation case like Jack, ceo of apple;
        if not (title_span[0].is_punct and title_span[0].lemma_ == ","):
            return None
        title_span = remove_det_punt_for_title_span(doc, title_span, start, end)
        if title_span != None and len(title_span) == 0:
            title_span = None
    return title_span
    
# deal with the before situation like
## U.S. Treasury Secretary Steven Mnuchin
## Redskins pregame host Kevin Sheehan 
def method_noun_chunk(doc, start, end, debug=False):
    title_span = None
    for chunk in doc.noun_chunks:
        if debug:
            print(chunk.text, chunk[0].i,  chunk[-1].i + 1, start, end)
        if chunk[0].i < start and chunk[-1].i + 1 >= end:
            if debug:
                debug_show_tokens_in_span(chunk)
            title_span = doc[chunk[0].i: start]            
            break;
    # normalize to None
    title_span = error_check_method_noun_chunk(title_span)
    if title_span is not None and len(title_span) == 0:
        title_span = None
    return title_span


# deal with case: Justin Williams is a Canadian-American professional ice hockey right winger 
def method_search_noun_chunk(doc, start, end, debug=False):
    title_span = None
    cdts = []
    for chunk in doc.noun_chunks:
        if chunk[-1].text.lower() in title_set:
            cdts.append(chunk)
    for chunk in cdts:
        ancestor = doc[end-1].head
        if debug:
             print(chunk[-1].head == ancestor, ancestor.lemma_)
        # To deal with James is player.
        if chunk[-1].head == ancestor and  ancestor.lemma_ == "be":
            title_span = chunk
        elif chunk[-1].i + 1 == start:
            title_span = chunk
    title_span = error_check_method_noun_chunk(title_span)
    title_span = remove_det_punt_for_title_span(doc, title_span, start, end)       
    return title_span
            
            
            
def error_check_method_noun_chunk(title_span):
    # if starts with punt which probably error in synatic parsing
    # case: (left) greets Nebraska coach Tim Miles
    i, last_i = 0, 0
    if title_span is None:
        return None
    if title_span[0].is_punct:
        #print(title_span)
        while(i< len(title_span)):
            if title_span[i].pos_ == 'VERB':
                last_i = i + 1
            i += 1
        if last_i < len(title_span):
            title_span = title_span[last_i:]
    # if too long, most likely it's wrong.
    #debug_show_tokens_in_span(title_span)
    elif len(title_span) > 10 or len(title_span) == 0:
        #debug_show_tokens_in_span(title_span)
        title_span = None
    return title_span
            

def extract_title_name(sent, debug=False):
    doc = nlp(sent)
    start, end = None, None
    name, title = None, None
    for ent in doc.ents:
        if ent.label_ == 'PERSON':
            name = ent.text
            start, end = ent[0].i, ent[-1].i + 1
    if start == None:
        return name, title
    # step 1 use noun_chunks
    title_span = method_noun_chunk(doc, start, end, debug)
    if title_span is None:
        if debug:
            print("[Noun Chunk]: None")
        # step 2 use dependency parsing
        title_span = method_depend_parsing(doc, start, end, debug)
    if title_span is None:
        if debug:
            print("[Depend Parsing]: None")
        # step 3 use noun chunk search
        title_span = method_search_noun_chunk(doc, start, end, debug)
    if title_span is not None:
        title = title_span.text
        # last to precoss
        title = post_process(title)
    return name, title

@app.route("/extract",methods=["GET", "POST"])
def extract():
    json_d = request.get_json(force=True)
    sentence = json_d.get('text','')
    name, title = None, None
    try:
        name, title = extract_title_name(sentence)
    except:
        print("[Error!]", sentence)
    if name == None:
        name = "null"
    if title == None:
        title == "null"
    return jsonify({
            "name":name,
            "title":title
        })

@app.route("/submit",methods=["GET", "POST"])
def submit():
    json_d = request.get_json(force=True)
    text = json_d.get('text', '')
    name = json_d.get('name', '')
    title = json_d.get('title', '')
    type = json_d.get('type', '')
    code = 'failed'
    msg = 'Failed!'
    if name is None or title is None \
            or len(name.strip()) == 0 \
            or len(title.strip()) == 0:
        msg = "Name or Title can not be empty."
    elif name not in text:
        msg = "name dose not appear in the text!"
    else:
        code = 'ok'
        msg = "Succeed submit %s sample"%type
        conn.set(text, json.dumps(json_d))
    return jsonify({
            "code":code,
            "msg":msg
        })

if __name__ == "__main__":
    text = """U.S. Treasury Secretary Steven Mnuchin said on Saturday \
            that Washington wants to include a provision to deter currency \
            manipulation in future trade deals, including with Japan,\
            based on the currency chapter in the new deal to revamp NAFTA."""
    #name, title = extract_title_name(text)
    #print(name, title)
    app.run(host='0.0.0.0', port=8080)
