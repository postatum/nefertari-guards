from nefertari.elasticsearch import ES
from nefertari.utils import dictset


def includeme(config):
    Settings = dictset(config.registry.settings)
    ACLFilterES.setup(Settings)


class ACLFilterES(ES):
    """ Nefertari ES subclass that applies ACL filtering when
    '_principals' param is passed.
    """
    def build_search_params(self, params):
        """ Override to add ACL filter params when '_principals'
        param is passed.
        """
        _principals = params.pop('_principals', None)
        _params = super(ACLFilterES, self).build_search_params(params)

        if _principals:
            permissions_query = build_acl_query(_principals)
            _params['body'] = {'query': {'filtered': _params['body']}}
            _params['body']['query']['filtered'].update(permissions_query)

        return _params

    def get_collection(self, **params):
        _principals = params.get('_principals', None)
        objects = super(ACLFilterES, self).get_collection(**params)
        # TODO: Filter objects relationships here per-object
        return objects

    def get_resource(self, **kw):
        _principals = kw.pop('_principals', None)
        obj = super(ACLFilterES, self).get_resource(**kw)
        # TODO: Filter object relationships here
        return obj


def _build_acl_bool_terms(acl, action_obj):
    """ Build ACL bool filter from given Pyramid ACL.

    :param acl: Valid Pyramid ACL used to build a bool filter query.
    :param action_obj: Pyramid ACL action object (Allow, Deny)
    """
    from nefertari_guards import engine
    acl = engine.ACLField.stringify_acl(acl)
    action = engine.ACLField._stringify_action(action_obj)
    principals = sorted(set([ace['principal'] for ace in acl]))
    permissions = sorted(set([ace['permission'] for ace in acl]))
    return [
        {'term': {'_acl.action': action}},
        {'terms': {'_acl.principal': principals}},
        {'terms': {'_acl.permission': permissions}},
    ]


def _build_acl_from_principals(principals, action_obj):
    """ Build ACL with 'all' and 'view' permissions for which
    of :principals: controlled by :action_obj:.

    :param principals: List of valid Pyramid ACL principals for
        which ACL should be built.
    :param action_obj: Pyramid ACL action object (Allow, Deny) which
        should be used in all ACEs of ACL.
    :return: Valid Pyramid ACL.
    """
    from pyramid.security import ALL_PERMISSIONS
    acl = []
    for ident in principals:
        acl += [
            (action_obj, ident, ALL_PERMISSIONS),
            (action_obj, ident, 'view'),
        ]
    return acl


def build_acl_query(principals):
    """ Build ES query to filter collection by only getting items
    for which user has 'view' or 'all' permission and does not have
    any of these permissions denied.

    Object is shown when its ACL allows 'all' or 'view' permissions
    to any one of principals and doesn't deny 'all' or 'view' permissions
    to any one of principals.
    Order of ACEs in ACL doesn't affect filtering results.

    :param principals: List of valid Pyramid ACL principals for
        which object permissions should be allowed.
    :return: ES 'filter' query part.
    """
    from pyramid.security import Allow, Deny

    # Generate ACLs from principals
    allowed_acl = _build_acl_from_principals(principals, Allow)
    denied_acl = _build_acl_from_principals(principals, Deny)

    # Generate bool terms queries
    must = _build_acl_bool_terms(allowed_acl, Allow)
    must_not = _build_acl_bool_terms(denied_acl, Deny)

    def get_bool_filter(query_terms):
        return {
            'nested': {
                'path': '_acl',
                'filter': {'bool': {'must': query_terms}}
            }
        }

    return {
        'filter': {
            'bool': {
                'must': get_bool_filter(must),
                'must_not': get_bool_filter(must_not),
            }
        }
    }


def get_es_item_acl(item):
    """ Get item ACL and return objectified version or it. """
    from nefertari_guards import engine
    acl = getattr(item, '_acl', ())
    return engine.ACLField.objectify_acl([
        ace._data for ace in acl])
