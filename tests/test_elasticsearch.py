from mock import patch, call, Mock
from nefertari.utils import DataProxy

from nefertari_guards import elasticsearch as es


class TestESHelpers(object):

    @patch('nefertari_guards.elasticsearch.engine')
    def test_build_acl_bool_terms(self, mock_engine):
        from pyramid.security import Allow
        mock_engine.ACLField.stringify_acl.return_value = [
            {'principal': 'user', 'permission': 'view'},
            {'principal': 'admin', 'permission': 'create'},
            {'principal': 'admin', 'permission': 'view'},
        ]
        mock_engine.ACLField._stringify_action.return_value = 'allow'
        terms = es._build_acl_bool_terms('zoo', Allow)
        mock_engine.ACLField.stringify_acl.assert_called_once_with('zoo')
        mock_engine.ACLField._stringify_action.assert_called_once_with(Allow)
        assert len(terms) == 3
        assert {'term': {'_acl.action': 'allow'}} in terms
        assert {'terms': {'_acl.principal': ['admin', 'user']}} in terms
        assert {'terms': {'_acl.permission': ['create', 'view']}} in terms

    def test_build_acl_from_principals(self):
        from pyramid.security import Deny, ALL_PERMISSIONS
        acl = es._build_acl_from_principals(['admin', 'user'], Deny)
        assert (Deny, 'user', 'view') in acl
        assert (Deny, 'user', ALL_PERMISSIONS) in acl
        assert (Deny, 'admin', 'view') in acl
        assert (Deny, 'admin', ALL_PERMISSIONS) in acl

    @patch('nefertari_guards.elasticsearch._build_acl_bool_terms')
    @patch('nefertari_guards.elasticsearch._build_acl_from_principals')
    def test_build_acl_query(self, build_ids, build_terms):
        from pyramid.security import Deny, Allow
        build_ids.return_value = [(1, 2, 3)]
        build_terms.return_value = 'foo'
        query = es.build_acl_query(['user', 'admin'])
        build_ids.assert_has_calls([
            call(['user', 'admin'], Allow),
            call(['user', 'admin'], Deny),
        ])
        build_terms.assert_has_calls([
            call([(1, 2, 3)], Allow),
            call([(1, 2, 3)], Deny),
        ])
        must = must_not = {
            'nested': {
                'path': '_acl',
                'filter': {'bool': {'must': 'foo'}}
            }
        }
        assert query == {
            'filter': {
                'bool': {
                    'must': must,
                    'must_not': must_not
                }
            }
        }

    @patch('nefertari_guards.elasticsearch._check_permissions')
    def test_check_relations_permissions_dict(self, mock_check):
        mock_check.return_value = 1
        document = {
            'one': 2,
            'two': {},
            'three': ['foo', 'bar']
        }
        request = 'Foo'
        checked = es.check_relations_permissions(request, document)
        assert checked == {
            'one': 1,
            'two': 1,
            'three': [1, 1]
        }
        mock_check.assert_has_calls([
            call(request, 2),
            call(request, {}),
            call(request, 'foo'),
            call(request, 'bar'),
        ], any_order=True)

    @patch('nefertari_guards.elasticsearch._check_permissions')
    def test_check_relations_permissions_dataproxy(self, mock_check):
        mock_check.return_value = 1
        data = {
            'one': 2,
            'two': {},
            'three': ['foo', 'bar']
        }
        document = DataProxy(data)
        request = 'Foo'
        checked = es.check_relations_permissions(request, document)
        assert checked._data == {
            'one': 1,
            'two': 1,
            'three': [1, 1]
        }
        mock_check.assert_has_calls([
            call(request, 2),
            call(request, {}),
            call(request, 'foo'),
            call(request, 'bar'),
        ], any_order=True)

    @patch('nefertari_guards.elasticsearch._check_permissions')
    def test_check_relations_permissions_none_dropped(self, mock_check):
        mock_check.return_value = None
        document = {
            'one': 2,
            'two': {},
            'three': ['foo', 'bar']
        }
        checked = es.check_relations_permissions('Foo', document)
        assert checked == {
            'one': None,
            'two': None,
            'three': []
        }

    def test_check_permissions_invalid_doc(self):
        assert es._check_permissions(None, 1) == 1
        assert es._check_permissions(None, 'foo') == 'foo'
        assert es._check_permissions(None, {'id': 1}) == {'id': 1}

    @patch('nefertari_guards.elasticsearch.engine')
    @patch('nefertari_guards.elasticsearch.dictset')
    def test__check_permissions_check_failed(self, mock_set, mock_engine):
        request = Mock()
        request.has_permission.return_value = False
        document = {'_type': 'Story', '_acl': ['foobar']}
        assert es._check_permissions(request, document) is None
        mock_engine.ACLField.objectify_acl.assert_called_once_with(
            ['foobar'])
        objectified = mock_engine.ACLField.objectify_acl()
        mock_set.assert_called_once_with({'__acl__': objectified})
        request.has_permission.assert_any_call('view', mock_set())

    @patch('nefertari_guards.elasticsearch.check_relations_permissions')
    @patch('nefertari_guards.elasticsearch.engine')
    @patch('nefertari_guards.elasticsearch.dictset')
    def test__check_permissions_check_succeeded(
            self, mock_set, mock_engine, mock_check):
        request = Mock()
        request.has_permission.return_value = True
        document = {'_type': 'Story', '_acl': ['foobar']}
        result = es._check_permissions(request, document)
        mock_engine.ACLField.objectify_acl.assert_called_once_with(
            ['foobar'])
        objectified = mock_engine.ACLField.objectify_acl()
        mock_set.assert_called_once_with({'__acl__': objectified})
        request.has_permission.assert_any_call('view', mock_set())
        mock_check.assert_called_once_with(request, document)
        assert result == mock_check()


class TestACLFilterES(object):

    @patch('nefertari_guards.elasticsearch.build_acl_query')
    def test_build_search_params(self, mock_build):
        obj = es.ACLFilterES('Foo', 'foondex', chunk_size=10)
        mock_build.return_value = {'filter': 'zoo'}
        params = obj.build_search_params(
            {'foo': 1, '_limit': 10, '_principals': [3, 4]})
        assert sorted(params.keys()) == sorted([
            'body', 'doc_type', 'from_', 'size', 'index'])
        assert params['body'] == {
            'query': {
                'filtered': {
                    'filter': 'zoo',
                    'query': {'query_string': {'query': 'foo:1'}}
                }
            }
        }
        assert params['index'] == 'foondex'
        assert params['doc_type'] == 'Foo'
        mock_build.assert_called_once_with([3, 4])

    @patch('nefertari_guards.elasticsearch.check_relations_permissions')
    @patch('nefertari_guards.elasticsearch.ES.get_collection')
    def test_get_collection_no_request(self, mock_get, mock_filter):
        obj = es.ACLFilterES('Foo', 'foondex', chunk_size=10)
        obj.get_collection(foo=1)
        mock_get.assert_called_once_with(foo=1)
        assert not mock_filter.called

    @patch('nefertari_guards.elasticsearch.check_relations_permissions')
    @patch('nefertari_guards.elasticsearch.ES.get_collection')
    def test_get_collection_no_auth(self, mock_get, mock_filter):
        obj = es.ACLFilterES('Foo', 'foondex', chunk_size=10)
        request = Mock()
        request.registry.settings = {'auth': 'false'}
        obj.get_collection(foo=1)
        mock_get.assert_called_once_with(foo=1)
        assert not mock_filter.called

    @patch('nefertari_guards.elasticsearch.check_relations_permissions')
    @patch('nefertari_guards.elasticsearch.ES.get_collection')
    def test_get_collection_auth(self, mock_get, mock_filter):
        mock_get.return_value = [1, 2]
        obj = es.ACLFilterES('Foo', 'foondex', chunk_size=10)
        request = Mock(effective_principals=['user', 'admin'])
        request.registry.settings = {'auth': 'true'}
        obj.get_collection(request=request, foo=1)
        mock_get.assert_called_once_with(
            foo=1, _principals=['user', 'admin'])
        mock_filter.assert_has_calls([
            call(request, 1),
            call(request, 2),
        ])

    @patch('nefertari_guards.elasticsearch.check_relations_permissions')
    @patch('nefertari_guards.elasticsearch.ES.get_resource')
    def test_get_resource_no_request(self, mock_get, mock_filter):
        obj = es.ACLFilterES('Foo', 'foondex', chunk_size=10)
        obj.get_resource(foo=1)
        mock_get.assert_called_once_with(foo=1)
        assert not mock_filter.called

    @patch('nefertari_guards.elasticsearch.check_relations_permissions')
    @patch('nefertari_guards.elasticsearch.ES.get_resource')
    def test_get_resource_no_auth(self, mock_get, mock_filter):
        obj = es.ACLFilterES('Foo', 'foondex', chunk_size=10)
        request = Mock()
        request.registry.settings = {'auth': 'false'}
        obj.get_resource(foo=1)
        mock_get.assert_called_once_with(foo=1)
        assert not mock_filter.called

    @patch('nefertari_guards.elasticsearch.check_relations_permissions')
    @patch('nefertari_guards.elasticsearch.ES.get_resource')
    def test_get_resource_auth(self, mock_get, mock_filter):
        mock_get.return_value = 1
        obj = es.ACLFilterES('Foo', 'foondex', chunk_size=10)
        request = Mock()
        request.registry.settings = {'auth': 'true'}
        obj.get_resource(request=request, foo=1)
        mock_get.assert_called_once_with(foo=1)
        mock_filter.assert_called_once_with(request, 1)
