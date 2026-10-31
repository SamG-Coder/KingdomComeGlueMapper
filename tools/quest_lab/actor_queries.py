"""Inventory authored actor/place graph queries without inventing engine defaults."""
from quest_import import literal


def inspect(model):
    result = []

    def visit(node, document, tree, path):
        if node['op'] == 'GraphSearch':
            filters = []

            def collect(child, location):
                # Nested graph searches have a different traversal origin.
                if child['op'] == 'GraphSearch': return
                if child['op'] in ('LinkDataFilter', 'LinkTagFilter'):
                    filters.append(dict(path=location, operation=child['op'],
                        attributes={k:literal(v) for k,v in child.get('attributes',{}).items()}))
                for i,c in enumerate(child.get('children',[])): collect(c,location+'/'+str(i))

            for i,c in enumerate(node.get('children',[])): collect(c,path+'/'+str(i))
            if filters:
                attrs = {k:literal(v) for k,v in node.get('attributes',{}).items()}
                result.append(dict(quest=model['quest'],document=document,tree=tree,path=path,
                    search=attrs,filters=filters,
                    origin_requires_engine_context=not bool(attrs.get('Origin')),
                    executed=False))
        for i,child in enumerate(node.get('children',[])):
            visit(child,document,tree,path+'/'+str(i))

    for document,data in sorted(model['behavior_documents'].items()):
        for tree,definition in sorted(data['trees'].items()):
            visit(definition['root'],document,tree,'Root')
    return result
