from __future__ import absolute_import

import logging
from typing import (Any, Dict, Iterable, List,  # pylint: disable=unused-import
                    MutableMapping, MutableSequence, Optional, Tuple, Union,
                    cast)

import rdflib
import rdflib.namespace
from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS
import six
from six.moves import urllib
from typing import Text  # pylint: disable=unused-import
# move to a regular typing import when Python 3.3-3.6 is no longer supported

from .ref_resolver import ContextType  # pylint: disable=unused-import
from .utils import aslist, json_dumps


_logger = logging.getLogger("salad")


def pred(datatype,      # type: MutableMapping[Text, Union[Dict, Text]]
         field,         # type: Optional[Dict]
         name,          # type: str
         context,       # type: ContextType
         defaultBase,   # type: str
         namespaces     # type: Dict[Text, rdflib.namespace.Namespace]
        ):  # type: (...) -> Union[Dict, Text]
    split = urllib.parse.urlsplit(name)

    vee = None  # type: Optional[Text]

    if split.scheme != '':
        vee = name
        (ns, ln) = rdflib.namespace.split_uri(six.text_type(vee))
        name = ln
        if ns[0:-1] in namespaces:
            vee = six.text_type(namespaces[ns[0:-1]][ln])
        _logger.debug("name, v %s %s", name, vee)

    v = None  # type: Optional[Dict]

    if field is not None and "jsonldPredicate" in field:
        if isinstance(field["jsonldPredicate"], MutableMapping):
            v = {}
            for k, val in field["jsonldPredicate"].items():
                v[("@" + k[1:] if k.startswith("_") else k)] = val
            if "@id" not in v:
                v["@id"] = vee
        else:
            v = field["jsonldPredicate"]
    elif "jsonldPredicate" in datatype:
        if isinstance(datatype["jsonldPredicate"], Iterable):
            for d in datatype["jsonldPredicate"]:
                if isinstance(d, MutableMapping):
                    if d["symbol"] == name:
                        v = d["predicate"]
                else:
                    raise Exception(
                        "entries in the jsonldPredicate List must be "
                        "Dictionaries")
        else:
            raise Exception("jsonldPredicate must be a List of Dictionaries.")

    ret = v or vee

    if not ret:
        ret = defaultBase + name

    if name in context:
        if context[name] != ret:
            raise Exception("Predicate collision on %s, '%s' != '%s'" %
                            (name, context[name], ret))
    else:
        _logger.debug("Adding to context '%s' %s (%s)", name, ret, type(ret))
        context[name] = ret

    return ret


def process_type(t,             # type: MutableMapping[Text, Any]
                 g,             # type: Graph
                 context,       # type: ContextType
                 defaultBase,   # type: str
                 namespaces,    # type: Dict[Text, rdflib.namespace.Namespace]
                 defaultPrefix  # type: str
                ):  # type: (...) -> None
    if t["type"] not in ("record", "enum"):
        return

    if "name" in t:
        recordname = t["name"]

        _logger.debug("Processing %s %s\n", t.get("type"), t)

        classnode = URIRef(recordname)
        g.add((classnode, RDF.type, RDFS.Class))

        split = urllib.parse.urlsplit(recordname)
        predicate = recordname
        if t.get("inVocab", True):
            if split.scheme:
                (ns, ln) = rdflib.namespace.split_uri(six.text_type(recordname))
                predicate = recordname
                recordname = ln
            else:
                predicate = "%s:%s" % (defaultPrefix, recordname)

        if context.get(recordname, predicate) != predicate:
            raise Exception("Predicate collision on '%s', '%s' != '%s'" % (
                recordname, context[recordname], predicate))

        if not recordname:
            raise Exception()

        _logger.debug("Adding to context '%s' %s (%s)",
                      recordname, predicate, type(predicate))
        context[recordname] = predicate

    if t["type"] == "record":
        for i in t.get("fields", []):
            fieldname = i["name"]

            _logger.debug("Processing field %s", i)

            v = pred(t, i, fieldname, context, defaultPrefix,
                     namespaces)  # type: Union[Dict[Any, Any], Text, None]

            if isinstance(v, six.string_types):
                v = v if v[0] != "@" else None
            elif v is not None:
                v = v["_@id"] if v.get("_@id", "@")[0] != "@" else None

            if bool(v):
                (ns, ln) = rdflib.namespace.split_uri(six.text_type(v))
                if ns[0:-1] in namespaces:
                    propnode = namespaces[ns[0:-1]][ln]
                else:
                    propnode = URIRef(v)

                g.add((propnode, RDF.type, RDF.Property))
                g.add((propnode, RDFS.domain, classnode))

                # TODO generate range from datatype.

            if isinstance(i["type"], MutableMapping):
                process_type(i["type"], g, context, defaultBase,
                             namespaces, defaultPrefix)

        if "extends" in t:
            for e in aslist(t["extends"]):
                g.add((classnode, RDFS.subClassOf, URIRef(e)))
    elif t["type"] == "enum":
        _logger.debug("Processing enum %s", t.get("name"))

        for i in t["symbols"]:
            pred(t, None, i, context, defaultBase, namespaces)


def salad_to_jsonld_context(j, schema_ctx):
    # type: (Iterable, MutableMapping[Text, Any]) -> Tuple[ContextType, Graph]
    context = {}  # type: ContextType
    namespaces = {}
    g = Graph()
    defaultPrefix = ""

    for k, v in schema_ctx.items():
        context[k] = v
        namespaces[k] = rdflib.namespace.Namespace(v)

    if "@base" in context:
        defaultBase = cast(str, context["@base"])
        del context["@base"]
    else:
        defaultBase = ""

    for k, v in namespaces.items():
        g.bind(str(k), v)

    for t in j:
        process_type(t, g, context, defaultBase, namespaces, defaultPrefix)

    return (context, g)


def fix_jsonld_ids(obj,  # type: Union[Dict[Text, Any], List[Dict[Text, Any]]]
                   ids   # type: List[Text]
                  ):  # type: (...) -> None
    if isinstance(obj, MutableMapping):
        for i in ids:
            if i in obj:
                obj["@id"] = obj[i]
        for v in obj.values():
            fix_jsonld_ids(v, ids)
    if isinstance(obj, MutableSequence):
        for entry in obj:
            fix_jsonld_ids(entry, ids)


def makerdf(workflow,       # type: Text
            wf,             # type: Union[List[Dict[Text, Any]], Dict[Text, Any]]
            ctx,            # type: ContextType
            graph=None      # type: Graph
           ):  # type: (...) -> Graph
    prefixes = {}
    idfields = []
    for k, v in six.iteritems(ctx):
        if isinstance(v, MutableMapping):
            url = v["@id"]
        else:
            url = v
        if url == "@id":
            idfields.append(k)
        doc_url, frg = urllib.parse.urldefrag(url)
        if "/" in frg:
            p = frg.split("/")[0]
            prefixes[p] = u"%s#%s/" % (doc_url, p)

    fix_jsonld_ids(wf, idfields)

    if graph is None:
        g = Graph()
    else:
        g = graph

    if isinstance(wf, MutableSequence):
        for w in wf:
            w["@context"] = ctx
            g.parse(data=json_dumps(w), format='json-ld', publicID=str(workflow))
    else:
        wf["@context"] = ctx
        g.parse(data=json_dumps(wf), format='json-ld', publicID=str(workflow))

    # Bug in json-ld loader causes @id fields to be added to the graph
    for sub, pred, obj in g.triples((None, URIRef("@id"), None)):
        g.remove((sub, pred, obj))

    for k2, v2 in six.iteritems(prefixes):
        g.namespace_manager.bind(k2, v2)

    return g
