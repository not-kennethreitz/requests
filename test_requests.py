#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for Requests."""

from __future__ import division
import json
import os
import pickle
import unittest
import collections

import io
import requests
import pytest
from requests.adapters import HTTPAdapter
from requests.auth import HTTPDigestAuth, _basic_auth_str
from requests.compat import (
    Morsel, cookielib, getproxies, str, urljoin, urlparse, is_py3, builtin_str)
from requests.cookies import cookiejar_from_dict, morsel_to_cookie
from requests.exceptions import (ConnectionError, ConnectTimeout,
                                 InvalidSchema, InvalidURL, MissingSchema,
                                 ReadTimeout, Timeout)
from requests.models import PreparedRequest
from requests.structures import CaseInsensitiveDict
from requests.sessions import SessionRedirectMixin
from requests.models import urlencode
from requests.hooks import default_hooks
from requests.utils import iter_slices
import asyncio
import functools

try:
    import StringIO
except ImportError:
    import io as StringIO

if is_py3:
    def u(s):
        return s
else:
    def u(s):
        return s.decode('unicode-escape')

def async_test(f):

    testLoop = asyncio.get_event_loop()
    testLoop.set_debug(True)

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        testLoop.run_until_complete(future)
    return wrapper

async_test.__test__ = False # not a test


# Requests to this URL should always fail with a connection timeout (nothing
# listening on that port)
TARPIT = "http://10.255.255.1"
HTTPBIN = os.environ.get('HTTPBIN_URL', 'http://httpbin.org/')
# Issue #1483: Make sure the URL always has a trailing slash
HTTPBIN = HTTPBIN.rstrip('/') + '/'


def httpbin(*suffix):
    """Returns url for HTTPBIN resource."""
    return urljoin(HTTPBIN, '/'.join(suffix))


class RequestsTestCase(unittest.TestCase):

    _multiprocess_can_split_ = True

    def setUp(self):
        """Create simple data set with headers."""
        pass

    def tearDown(self):
        """Teardown."""
        pass

    def test_entry_points(self):

        requests.session
        requests.session().get
        requests.session().head
        requests.get
        requests.head
        requests.put
        requests.patch
        requests.post

    @async_test
    def test_invalid_url(self):
        with pytest.raises(MissingSchema):
            yield from requests.get('hiwpefhipowhefopw')
        with pytest.raises(InvalidSchema):
            yield from requests.get('localhost:3128')
        with pytest.raises(InvalidSchema):
            yield from requests.get('localhost.localdomain:3128/')
        with pytest.raises(InvalidSchema):
            yield from requests.get('10.122.1.1:3128/')
        with pytest.raises(InvalidURL):
            yield from requests.get('http://')

    @async_test
    def test_basic_building(self):
        req = requests.Request()
        req.url = 'http://kennethreitz.org/'
        req.data = {'life': '42'}

        pr = req.prepare()
        assert pr.url == req.url
        assert pr.body == 'life=42'

    @async_test
    def test_no_content_length(self):
        get_req = requests.Request('GET', httpbin('get')).prepare()
        assert 'Content-Length' not in get_req.headers
        head_req = requests.Request('HEAD', httpbin('head')).prepare()
        assert 'Content-Length' not in head_req.headers

    @async_test
    def test_path_is_not_double_encoded(self):
        request = requests.Request('GET', "http://0.0.0.0/get/test case").prepare()

        assert request.path_url == '/get/test%20case'

    @async_test
    def test_params_are_added_before_fragment(self):
        request = requests.Request('GET',
            "http://example.com/path#fragment", params={"a": "b"}).prepare()
        assert request.url == "http://example.com/path?a=b#fragment"
        request = requests.Request('GET',
            "http://example.com/path?key=value#fragment", params={"a": "b"}).prepare()
        assert request.url == "http://example.com/path?key=value&a=b#fragment"

    @async_test
    def test_mixed_case_scheme_acceptable(self):
        s = requests.Session()
        s.proxies = getproxies()
        parts = urlparse(httpbin('get'))
        schemes = ['http://', 'HTTP://', 'hTTp://', 'HttP://',
                   'https://', 'HTTPS://', 'hTTps://', 'HttPs://']
        for scheme in schemes:
            url = scheme + parts.netloc + parts.path
            r = requests.Request('GET', url)
            r = yield from s.send(r.prepare())
            assert r.status_code == 200, 'failed for scheme {0}'.format(scheme)

    @async_test
    def test_HTTP_200_OK_GET_ALTERNATIVE(self):
        r = requests.Request('GET', httpbin('get'))
        s = requests.Session()
        s.proxies = getproxies()

        r = yield from s.send(r.prepare())

        assert r.status_code == 200

    @async_test
    def test_HTTP_302_ALLOW_REDIRECT_GET(self):
        r = yield from requests.get(httpbin('redirect', '1'))
        assert r.status_code == 200
        assert r.history[0].status_code == 302
        assert r.history[0].is_redirect

    @async_test
    def test_HTTP_302_ALLOW_REDIRECT_POST(self):
         r = yield from requests.post(httpbin('status', '302'), data={'some': 'data'})
         self.assertEqual(r.status_code, 200)

    @async_test
    def test_HTTP_200_OK_GET_WITH_PARAMS(self):
        heads = {'User-agent': 'Mozilla/5.0'}

        r = yield from requests.get(httpbin('user-agent'), headers=heads)

        rText = yield from r.text
        assert heads['User-agent'] in rText
        assert r.status_code == 200

    @async_test
    def test_HTTP_200_OK_GET_WITH_MIXED_PARAMS(self):
        heads = {'User-agent': 'Mozilla/5.0'}

        r = yield from requests.get(httpbin('get') + '?test=true', params={'q': 'test'}, headers=heads)
        assert r.status_code == 200

    @async_test
    def test_set_cookie_on_301(self):
        s = requests.session()
        url = httpbin('cookies/set?foo=bar')
        yield from s.get(url)
        assert s.cookies['foo'] == 'bar'

    @async_test
    def test_cookie_sent_on_redirect(self):
        s = requests.session()
        yield from s.get(httpbin('cookies/set?foo=bar'))
        r = yield from s.get(httpbin('redirect/1'))  # redirects to httpbin('get')
        rJson = yield from r.json()
        assert 'Cookie' in rJson['headers']

    @async_test
    def test_cookie_removed_on_expire(self):
        s = requests.session()
        yield from s.get(httpbin('cookies/set?foo=bar'))
        assert s.cookies['foo'] == 'bar'
        yield from s.get(
            httpbin('response-headers'),
            params={
                'Set-Cookie':
                    'foo=deleted; expires=Thu, 01-Jan-1970 00:00:01 GMT'
            }
        )
        assert 'foo' not in s.cookies

    @async_test
    def test_cookie_quote_wrapped(self):
        s = requests.session()
        yield from s.get(httpbin('cookies/set?foo="bar:baz"'))
        assert s.cookies['foo'] == '"bar:baz"'

    @async_test
    def test_cookie_persists_via_api(self):
        s = requests.session()
        r = yield from s.get(httpbin('redirect/1'), cookies={'foo': 'bar'})
        assert 'foo' in r.request.headers['Cookie']
        assert 'foo' in r.history[0].request.headers['Cookie']

    @async_test
    def test_request_cookie_overrides_session_cookie(self):
        s = requests.session()
        s.cookies['foo'] = 'bar'
        r = yield from s.get(httpbin('cookies'), cookies={'foo': 'baz'})
        rJson = yield from r.json()
        assert rJson['cookies']['foo'] == 'baz'
        # Session cookie should not be modified
        assert s.cookies['foo'] == 'bar'

    @async_test
    def test_request_cookies_not_persisted(self):
        s = requests.session()
        yield from s.get(httpbin('cookies'), cookies={'foo': 'baz'})
        # Sending a request with cookies should not add cookies to the session
        assert not s.cookies

    @async_test
    def test_generic_cookiejar_works(self):
        cj = cookielib.CookieJar()
        cookiejar_from_dict({'foo': 'bar'}, cj)
        s = requests.session()
        s.cookies = cj
        r = yield from s.get(httpbin('cookies'))
        # Make sure the cookie was sent
        rJson = yield from r.json()
        assert rJson['cookies']['foo'] == 'bar'
        # Make sure the session cj is still the custom one
        assert s.cookies is cj

    @async_test
    def test_param_cookiejar_works(self):
        cj = cookielib.CookieJar()
        cookiejar_from_dict({'foo': 'bar'}, cj)
        s = requests.session()
        r = yield from s.get(httpbin('cookies'), cookies=cj)
        # Make sure the cookie was sent
        rJson = yield from r.json()
        assert rJson['cookies']['foo'] == 'bar'

    @async_test
    def test_requests_in_history_are_not_overridden(self):
        resp = yield from requests.get(httpbin('redirect/3'))
        urls = [r.url for r in resp.history]
        req_urls = [r.request.url for r in resp.history]
        assert urls == req_urls

    @async_test
    def test_history_is_always_a_list(self):
        """
        Show that even with redirects, Response.history is always a list.
        """
        resp = yield from requests.get(httpbin('get'))
        assert isinstance(resp.history, list)
        resp = yield from requests.get(httpbin('redirect/1'))
        assert isinstance(resp.history, list)
        assert not isinstance(resp.history, tuple)

    @async_test
    def test_headers_on_session_with_None_are_not_sent(self):
        """Do not send headers in Session.headers with None values."""
        ses = requests.Session()
        ses.headers['Accept-Encoding'] = None
        req = requests.Request('GET', 'http://httpbin.org/get')
        prep = ses.prepare_request(req)
        assert 'Accept-Encoding' not in prep.headers

    @async_test
    def test_user_agent_transfers(self):

        heads = {
            'User-agent': 'Mozilla/5.0 (github.com/kennethreitz/requests)'
        }

        r = yield from requests.get(httpbin('user-agent'), headers=heads)
        rText = yield from r.text
        assert heads['User-agent'] in rText

        heads = {
            'user-agent': 'Mozilla/5.0 (github.com/kennethreitz/requests)'
        }

        r = yield from requests.get(httpbin('user-agent'), headers=heads)
        rText = yield from r.text
        assert heads['user-agent'] in rText

    @async_test
    def test_HTTP_200_OK_HEAD(self):
        r = yield from requests.head(httpbin('get'))
        assert r.status_code == 200

    @async_test
    def test_HTTP_200_OK_PUT(self):
        r = yield from requests.put(httpbin('put'))
        assert r.status_code == 200

    @async_test
    def test_BASICAUTH_TUPLE_HTTP_200_OK_GET(self):
        auth = ('user', 'pass')
        url = httpbin('basic-auth', 'user', 'pass')

        r = yield from requests.get(url, auth=auth)
        assert r.status_code == 200

        r = yield from requests.get(url)
        assert r.status_code == 401

        s = requests.session()
        s.auth = auth
        r = yield from s.get(url)
        assert r.status_code == 200

    @async_test
    def test_connection_error(self):
        """Connecting to an unknown domain should raise a ConnectionError"""
        with pytest.raises(ConnectionError):
            yield from requests.get("http://fooobarbangbazbing.httpbin.org")

        with pytest.raises(ConnectionError):
            yield from requests.get("http://httpbin.org:1")

    @async_test
    def test_basicauth_with_netrc(self):
        auth = ('user', 'pass')
        wrong_auth = ('wronguser', 'wrongpass')
        url = httpbin('basic-auth', 'user', 'pass')

        def get_netrc_auth_mock(url):
            return auth
        requests.sessions.get_netrc_auth = get_netrc_auth_mock

        # Should use netrc and work.
        r = yield from requests.get(url)
        assert r.status_code == 200

        # Given auth should override and fail.
        r = yield from requests.get(url, auth=wrong_auth)
        assert r.status_code == 401

        s = requests.session()

        # Should use netrc and work.
        r = yield from s.get(url)
        assert r.status_code == 200

        # Given auth should override and fail.
        s.auth = wrong_auth
        r = yield from s.get(url)
        assert r.status_code == 401

    @async_test
    def test_DIGEST_HTTP_200_OK_GET(self):

        auth = HTTPDigestAuth('user', 'pass')
        url = httpbin('digest-auth', 'auth', 'user', 'pass')

        r = yield from requests.get(url, auth=auth)
        assert r.status_code == 200

        r = yield from requests.get(url)
        assert r.status_code == 401

        s = requests.session()
        s.auth = HTTPDigestAuth('user', 'pass')
        r = yield from s.get(url)
        assert r.status_code == 200

    @async_test
    def test_DIGEST_AUTH_RETURNS_COOKIE(self):
        url = httpbin('digest-auth', 'auth', 'user', 'pass')
        auth = HTTPDigestAuth('user', 'pass')
        r = yield from requests.get(url)
        assert r.cookies['fake'] == 'fake_value'

        r = yield from requests.get(url, auth=auth)
        assert r.status_code == 200

    @async_test
    def test_DIGEST_AUTH_SETS_SESSION_COOKIES(self):
        url = httpbin('digest-auth', 'auth', 'user', 'pass')
        auth = HTTPDigestAuth('user', 'pass')
        s = requests.Session()
        yield from s.get(url, auth=auth)
        assert s.cookies['fake'] == 'fake_value'

    @async_test
    def test_DIGEST_STREAM(self):

        auth = HTTPDigestAuth('user', 'pass')
        url = httpbin('digest-auth', 'auth', 'user', 'pass')

        r = yield from requests.get(url, auth=auth, stream=True)
        assert (yield from r.raw.read()) != b''

        r = yield from requests.get(url, auth=auth, stream=False)
        assert (yield from r.raw.read()) == b''

    @async_test
    def test_DIGESTAUTH_WRONG_HTTP_401_GET(self):

        auth = HTTPDigestAuth('user', 'wrongpass')
        url = httpbin('digest-auth', 'auth', 'user', 'pass')

        r = yield from requests.get(url, auth=auth)
        assert r.status_code == 401

        r = yield from requests.get(url)
        assert r.status_code == 401

        s = requests.session()
        s.auth = auth
        r = yield from s.get(url)
        assert r.status_code == 401

    @async_test
    def test_DIGESTAUTH_QUOTES_QOP_VALUE(self):

        auth = HTTPDigestAuth('user', 'pass')
        url = httpbin('digest-auth', 'auth', 'user', 'pass')

        r = yield from requests.get(url, auth=auth)
        assert '"auth"' in r.request.headers['Authorization']

    @async_test
    def test_POSTBIN_GET_POST_FILES(self):

        url = httpbin('post')
        post1 = (yield from requests.post(url)).raise_for_status()

        post1 = yield from requests.post(url, data={'some': 'data'})
        assert post1.status_code == 200

        with open('requirements.txt') as f:
            post2 = yield from requests.post(url, files={'some': f})
        assert post2.status_code == 200

        post4 = yield from requests.post(url, data='[{"some": "json"}]')
        assert post4.status_code == 200

        with pytest.raises(ValueError):
            yield from requests.post(url, files=['bad file data'])

    @async_test
    def test_POSTBIN_GET_POST_FILES_WITH_DATA(self):

        url = httpbin('post')
        post1 = (yield from requests.post(url)).raise_for_status()

        post1 = yield from requests.post(url, data={'some': 'data'})
        assert post1.status_code == 200

        with open('requirements.txt') as f:
            post2 = yield from requests.post(url,
                data={'some': 'data'}, files={'some': f})
        assert post2.status_code == 200

        post4 = yield from requests.post(url, data='[{"some": "json"}]')
        assert post4.status_code == 200

        with pytest.raises(ValueError):
            yield from requests.post(url, files=['bad file data'])

    @async_test
    def test_conflicting_post_params(self):
        url = httpbin('post')
        with open('requirements.txt') as f:
            try:
                yield from requests.post(url, data='[{\"some\": \"data\"}]', files={'some': f})
            except ValueError:
                pass
            else:
                self.fail('did not raise %s' % ValueError)
        with open('requirements.txt') as f:
            try:
                yield from requests.post(url, data=u('[{\"some\": \"data\"}]'), files={'some': f})
            except ValueError:
                pass
            else:
                self.fail('did not raise %s' % ValueError)

    @async_test
    def test_request_ok_set(self):
        r = yield from requests.get(httpbin('status', '404'))
        assert not r.ok

    @async_test
    def test_status_raising(self):
        r = yield from requests.get(httpbin('status', '404'))
        with pytest.raises(requests.exceptions.HTTPError):
            r.raise_for_status()

        r = yield from requests.get(httpbin('status', '500'))
        assert not r.ok

    @async_test
    def tst_decompress_gzip(self):
        r = yield from requests.get(httpbin('gzip'))
        rc = yield from r.content
        rc.decode('ascii')

    @async_test
    def test_unicode_get(self):
        url = httpbin('/get')
        yield from requests.get(url, params={'foo': 'føø'})
        yield from requests.get(url, params={'føø': 'føø'})
        yield from requests.get(url, params={'føø': 'føø'})
        yield from requests.get(url, params={'foo': 'foo'})
        yield from requests.get(httpbin('ø'), params={'foo': 'foo'})

    @async_test
    def test_unicode_header_name(self):
        requests.put(
            httpbin('put'),
            headers={str('Content-Type'): 'application/octet-stream'},
            data='\xff')  # compat.str is unicode.

    @async_test
    def test_pyopenssl_redirect(self):
        yield from requests.get('https://httpbin.org/status/301')

    @async_test
    def test_urlencoded_get_query_multivalued_param(self):

        r = yield from requests.get(httpbin('get'), params=dict(test=['foo', 'baz']))
        assert r.status_code == 200
        assert r.url == httpbin('get?test=foo&test=baz')

    @async_test
    def test_different_encodings_dont_break_post(self):
        r = yield from requests.post(httpbin('post'),
            data={'stuff': json.dumps({'a': 123})},
            params={'blah': 'asdf1234'},
            files={'file': ('test_requests.py', open(__file__, 'rb'))})
        assert r.status_code == 200

    @async_test
    def test_unicode_multipart_post(self):
        r = yield from requests.post(httpbin('post'),
            data={'stuff': u('ëlïxr')},
            files={'file': ('test_requests.py', open(__file__, 'rb'))})
        assert r.status_code == 200

        r = yield from requests.post(httpbin('post'),
            data={'stuff': u('ëlïxr').encode('utf-8')},
            files={'file': ('test_requests.py', open(__file__, 'rb'))})
        assert r.status_code == 200

        r = yield from requests.post(httpbin('post'),
            data={'stuff': 'elixr'},
            files={'file': ('test_requests.py', open(__file__, 'rb'))})
        assert r.status_code == 200

        r = yield from requests.post(httpbin('post'),
            data={'stuff': 'elixr'.encode('utf-8')},
            files={'file': ('test_requests.py', open(__file__, 'rb'))})
        assert r.status_code == 200

    @async_test
    def test_unicode_multipart_post_fieldnames(self):
        filename = os.path.splitext(__file__)[0] + '.py'
        r = requests.Request(method='POST',
                             url=httpbin('post'),
                             data={'stuff'.encode('utf-8'): 'elixr'},
                             files={'file': ('test_requests.py',
                                             open(filename, 'rb'))})
        prep = r.prepare()
        assert b'name="stuff"' in prep.body
        assert b'name="b\'stuff\'"' not in prep.body

    @async_test
    def test_unicode_method_name(self):
        files = {'file': open('test_requests.py', 'rb')}
        r = yield from requests.request(
            method=u('POST'), url=httpbin('post'), files=files)
        assert r.status_code == 200

    @async_test
    def test_custom_content_type(self):
        r = yield from requests.post(
            httpbin('post'),
            data={'stuff': json.dumps({'a': 123})},
            files={'file1': ('test_requests.py', open(__file__, 'rb')),
                   'file2': ('test_requests', open(__file__, 'rb'),
                             'text/py-content-type')})
        assert r.status_code == 200
        assert b"text/py-content-type" in r.request.body

    @async_test
    def test_hook_receives_request_arguments(self):
        def hook(resp, **kwargs):
            assert resp is not None
            assert kwargs != {}

        requests.Request('GET', HTTPBIN, hooks={'response': hook})

    @async_test
    def test_session_hooks_are_used_with_no_request_hooks(self):
        hook = lambda x, *args, **kwargs: x
        s = requests.Session()
        s.hooks['response'].append(hook)
        r = requests.Request('GET', HTTPBIN)
        prep = s.prepare_request(r)
        assert prep.hooks['response'] != []
        assert prep.hooks['response'] == [hook]

    @async_test
    def test_session_hooks_are_overriden_by_request_hooks(self):
        hook1 = lambda x, *args, **kwargs: x
        hook2 = lambda x, *args, **kwargs: x
        assert hook1 is not hook2
        s = requests.Session()
        s.hooks['response'].append(hook2)
        r = requests.Request('GET', HTTPBIN, hooks={'response': [hook1]})
        prep = s.prepare_request(r)
        assert prep.hooks['response'] == [hook1]

    @async_test
    def test_prepared_request_hook(self):
        def hook(resp, **kwargs):
            yield None # make hook a generator
            resp.hook_working = True
            return resp

        req = requests.Request('GET', HTTPBIN, hooks={'response': hook})
        prep = req.prepare()

        s = requests.Session()
        s.proxies = getproxies()
        resp = yield from s.send(prep)

        assert hasattr(resp, 'hook_working')

    @async_test
    def test_prepared_from_session(self):
        class DummyAuth(requests.auth.AuthBase):
            def __call__(self, r):
                r.headers['Dummy-Auth-Test'] = 'dummy-auth-test-ok'
                return r

        req = requests.Request('GET', httpbin('headers'))
        assert not req.auth

        s = requests.Session()
        s.auth = DummyAuth()

        prep = s.prepare_request(req)
        resp = yield from s.send(prep)

        rJson = yield from resp.json()
        assert rJson['headers'][
            'Dummy-Auth-Test'] == 'dummy-auth-test-ok'

    @async_test
    def test_prepare_request_with_bytestring_url(self):
        req = requests.Request('GET', b'https://httpbin.org/')
        s = requests.Session()
        prep = s.prepare_request(req)
        assert prep.url == "https://httpbin.org/"

    @async_test
    def test_links(self):
        r = requests.Response()
        r.headers = {
            'cache-control': 'public, max-age=60, s-maxage=60',
            'connection': 'keep-alive',
            'content-encoding': 'gzip',
            'content-type': 'application/json; charset=utf-8',
            'date': 'Sat, 26 Jan 2013 16:47:56 GMT',
            'etag': '"6ff6a73c0e446c1f61614769e3ceb778"',
            'last-modified': 'Sat, 26 Jan 2013 16:22:39 GMT',
            'link': ('<https://api.github.com/users/kennethreitz/repos?'
                     'page=2&per_page=10>; rel="next", <https://api.github.'
                     'com/users/kennethreitz/repos?page=7&per_page=10>; '
                     ' rel="last"'),
            'server': 'GitHub.com',
            'status': '200 OK',
            'vary': 'Accept',
            'x-content-type-options': 'nosniff',
            'x-github-media-type': 'github.beta',
            'x-ratelimit-limit': '60',
            'x-ratelimit-remaining': '57'
        }
        assert r.links['next']['rel'] == 'next'

    @async_test
    def test_cookie_parameters(self):
        key = 'some_cookie'
        value = 'some_value'
        secure = True
        domain = 'test.com'
        rest = {'HttpOnly': True}

        jar = requests.cookies.RequestsCookieJar()
        jar.set(key, value, secure=secure, domain=domain, rest=rest)

        assert len(jar) == 1
        assert 'some_cookie' in jar

        cookie = list(jar)[0]
        assert cookie.secure == secure
        assert cookie.domain == domain
        assert cookie._rest['HttpOnly'] == rest['HttpOnly']

    @async_test
    def test_cookie_as_dict_keeps_len(self):
        key = 'some_cookie'
        value = 'some_value'

        key1 = 'some_cookie1'
        value1 = 'some_value1'

        jar = requests.cookies.RequestsCookieJar()
        jar.set(key, value)
        jar.set(key1, value1)

        d1 = dict(jar)
        d2 = dict(jar.iteritems())
        d3 = dict(jar.items())

        assert len(jar) == 2
        assert len(d1) == 2
        assert len(d2) == 2
        assert len(d3) == 2

    @async_test
    def test_cookie_as_dict_keeps_items(self):
        key = 'some_cookie'
        value = 'some_value'

        key1 = 'some_cookie1'
        value1 = 'some_value1'

        jar = requests.cookies.RequestsCookieJar()
        jar.set(key, value)
        jar.set(key1, value1)

        d1 = dict(jar)
        d2 = dict(jar.iteritems())
        d3 = dict(jar.items())

        assert d1['some_cookie'] == 'some_value'
        assert d2['some_cookie'] == 'some_value'
        assert d3['some_cookie1'] == 'some_value1'

    @async_test
    def test_cookie_as_dict_keys(self):
        key = 'some_cookie'
        value = 'some_value'

        key1 = 'some_cookie1'
        value1 = 'some_value1'

        jar = requests.cookies.RequestsCookieJar()
        jar.set(key, value)
        jar.set(key1, value1)

        keys = jar.keys()
        assert keys == list(keys)
        # make sure one can use keys multiple times
        assert list(keys) == list(keys)

    @async_test
    def test_cookie_as_dict_values(self):
        key = 'some_cookie'
        value = 'some_value'

        key1 = 'some_cookie1'
        value1 = 'some_value1'

        jar = requests.cookies.RequestsCookieJar()
        jar.set(key, value)
        jar.set(key1, value1)

        values = jar.values()
        assert values == list(values)
        # make sure one can use values multiple times
        assert list(values) == list(values)

    @async_test
    def test_cookie_as_dict_items(self):
        key = 'some_cookie'
        value = 'some_value'

        key1 = 'some_cookie1'
        value1 = 'some_value1'

        jar = requests.cookies.RequestsCookieJar()
        jar.set(key, value)
        jar.set(key1, value1)

        items = jar.items()
        assert items == list(items)
        # make sure one can use items multiple times
        assert list(items) == list(items)

    @async_test
    def test_time_elapsed_blank(self):
        r = yield from requests.get(httpbin('get'))
        td = r.elapsed
        total_seconds = ((td.microseconds + (td.seconds + td.days * 24 * 3600)
                         * 10**6) / 10**6)
        assert total_seconds > 0.0

    @async_test
    def tst_response_is_iterable(self):
        r = requests.Response()
        io = StringIO.StringIO('abc')
        read_ = io.read

        def read_mock(amt, decode_content=None):
            return read_(amt)
        setattr(io, 'read', read_mock)
        r.raw = io
        assert next(iter(r))
        io.close()

    @async_test
    def test_response_decode_unicode(self):
        """
        When called with decode_unicode, Response.iter_content should always
        return unicode.
        """
        r = requests.Response()
        r._content_consumed = True
        r._content = b'the content'
        r.encoding = 'ascii'

        chunks = yield from r.iter_content(decode_unicode=True)
        assert all(isinstance(chunk, str) for chunk in chunks)

        class FakeIO():
            def __init__(self, d):
                self.d = d
            def stream(self, size):
                yield None
                return iter_slices(self.d, size)


        # also for streaming
        r = requests.Response()
        r.raw = FakeIO(b'the content')
        r.encoding = 'ascii'
        chunks = yield from r.iter_content(decode_unicode=True)
        assert all(isinstance(chunk, str) for chunk in chunks)

    @async_test
    def test_request_and_response_are_pickleable(self):
        r = yield from requests.get(httpbin('get'))

        # verify we can pickle the original request
        assert pickle.loads(pickle.dumps(r.request))

        # verify we can pickle the response and that we have access to
        # the original request.
        pr = pickle.loads(pickle.dumps(r))
        assert r.request.url == pr.request.url
        assert r.request.headers == pr.request.headers

    @async_test
    def test_get_auth_from_url(self):
        url = 'http://user:pass@complex.url.com/path?query=yes'
        assert ('user', 'pass') == requests.utils.get_auth_from_url(url)

    @async_test
    def test_get_auth_from_url_encoded_spaces(self):
        url = 'http://user:pass%20pass@complex.url.com/path?query=yes'
        assert ('user', 'pass pass') == requests.utils.get_auth_from_url(url)

    @async_test
    def test_get_auth_from_url_not_encoded_spaces(self):
        url = 'http://user:pass pass@complex.url.com/path?query=yes'
        assert ('user', 'pass pass') == requests.utils.get_auth_from_url(url)

    @async_test
    def test_get_auth_from_url_percent_chars(self):
        url = 'http://user%25user:pass@complex.url.com/path?query=yes'
        assert ('user%user', 'pass') == requests.utils.get_auth_from_url(url)

    @async_test
    def test_get_auth_from_url_encoded_hashes(self):
        url = 'http://user:pass%23pass@complex.url.com/path?query=yes'
        assert ('user', 'pass#pass') == requests.utils.get_auth_from_url(url)

    @async_test
    def test_cannot_send_unprepared_requests(self):
        r = requests.Request(url=HTTPBIN)
        with pytest.raises(ValueError):
            yield from requests.Session().send(r)

    @async_test
    def test_http_error(self):
        error = requests.exceptions.HTTPError()
        assert not error.response
        response = requests.Response()
        error = requests.exceptions.HTTPError(response=response)
        assert error.response == response
        error = requests.exceptions.HTTPError('message', response=response)
        assert str(error) == 'message'
        assert error.response == response

    @async_test
    def test_session_pickling(self):
        r = requests.Request('GET', httpbin('get'))
        s = requests.Session()

        s = pickle.loads(pickle.dumps(s))
        s.proxies = getproxies()

        r = yield from s.send(r.prepare())
        assert r.status_code == 200

    @async_test
    def test_fixes_1329(self):
        """
        Ensure that header updates are done case-insensitively.
        """
        s = requests.Session()
        s.headers.update({'ACCEPT': 'BOGUS'})
        s.headers.update({'accept': 'application/json'})
        r = yield from s.get(httpbin('get'))
        headers = r.request.headers
        assert headers['accept'] == 'application/json'
        assert headers['Accept'] == 'application/json'
        assert headers['ACCEPT'] == 'application/json'

    @async_test
    def test_uppercase_scheme_redirect(self):
        parts = urlparse(httpbin('html'))
        url = "HTTP://" + parts.netloc + parts.path
        r = yield from requests.get(httpbin('redirect-to'), params={'url': url})
        assert r.status_code == 200
        assert r.url.lower() == url.lower()

    @async_test
    def test_transport_adapter_ordering(self):
        s = requests.Session()
        order = ['https://', 'http://']
        assert order == list(s.adapters)
        s.mount('http://git', HTTPAdapter())
        s.mount('http://github', HTTPAdapter())
        s.mount('http://github.com', HTTPAdapter())
        s.mount('http://github.com/about/', HTTPAdapter())
        order = [
            'http://github.com/about/',
            'http://github.com',
            'http://github',
            'http://git',
            'https://',
            'http://',
        ]
        assert order == list(s.adapters)
        s.mount('http://gittip', HTTPAdapter())
        s.mount('http://gittip.com', HTTPAdapter())
        s.mount('http://gittip.com/about/', HTTPAdapter())
        order = [
            'http://github.com/about/',
            'http://gittip.com/about/',
            'http://github.com',
            'http://gittip.com',
            'http://github',
            'http://gittip',
            'http://git',
            'https://',
            'http://',
        ]
        assert order == list(s.adapters)
        s2 = requests.Session()
        s2.adapters = {'http://': HTTPAdapter()}
        s2.mount('https://', HTTPAdapter())
        assert 'http://' in s2.adapters
        assert 'https://' in s2.adapters

    @async_test
    def test_header_remove_is_case_insensitive(self):
        # From issue #1321
        s = requests.Session()
        s.headers['foo'] = 'bar'
        r = yield from s.get(httpbin('get'), headers={'FOO': None})
        assert 'foo' not in r.request.headers

    @async_test
    def test_params_are_merged_case_sensitive(self):
        s = requests.Session()
        s.params['foo'] = 'bar'
        r = yield from s.get(httpbin('get'), params={'FOO': 'bar'})
        rJson = yield from r.json()
        assert rJson['args'] == {'foo': 'bar', 'FOO': 'bar'}

    @async_test
    def test_long_authinfo_in_url(self):
        url = 'http://{0}:{1}@{2}:9000/path?query#frag'.format(
            'E8A3BE87-9E3F-4620-8858-95478E385B5B',
            'EA770032-DA4D-4D84-8CE9-29C6D910BF1E',
            'exactly-------------sixty-----------three------------characters',
        )
        r = requests.Request('GET', url).prepare()
        assert r.url == url

    @async_test
    def test_header_keys_are_native(self):
        headers = {u('unicode'): 'blah', 'byte'.encode('ascii'): 'blah'}
        r = requests.Request('GET', httpbin('get'), headers=headers)
        p = r.prepare()

        # This is testing that they are builtin strings. A bit weird, but there
        # we go.
        assert 'unicode' in p.headers.keys()
        assert 'byte' in p.headers.keys()

    @async_test
    def test_can_send_nonstring_objects_with_files(self):
        data = {'a': 0.0}
        files = {'b': 'foo'}
        r = requests.Request('POST', httpbin('post'), data=data, files=files)
        p = r.prepare()

        assert 'multipart/form-data' in p.headers['Content-Type']

    @async_test
    def test_autoset_header_values_are_native(self):
        data = 'this is a string'
        length = '16'
        req = requests.Request('POST', httpbin('post'), data=data)
        p = req.prepare()

        assert p.headers['Content-Length'] == length

    @async_test
    def test_nonhttp_schemes_dont_check_URLs(self):
        test_urls = (
            'data:image/gif;base64,R0lGODlhAQABAHAAACH5BAUAAAAALAAAAAABAAEAAAICRAEAOw==',
            'file:///etc/passwd',
            'magnet:?xt=urn:btih:be08f00302bc2d1d3cfa3af02024fa647a271431',
        )
        for test_url in test_urls:
            req = requests.Request('GET', test_url)
            preq = req.prepare()
            assert test_url == preq.url

    @async_test
    def test_auth_is_stripped_on_redirect_off_host(self):
        r = yield from requests.get(
            httpbin('redirect-to'),
            params={'url': 'http://www.google.co.uk'},
            auth=('user', 'pass'),
        )
        assert r.history[0].request.headers['Authorization']
        assert not r.request.headers.get('Authorization', '')

    @async_test
    def test_auth_is_retained_for_redirect_on_host(self):
        r = yield from requests.get(httpbin('redirect/1'), auth=('user', 'pass'))
        h1 = r.history[0].request.headers['Authorization']
        h2 = r.request.headers['Authorization']

        assert h1 == h2

    @async_test
    def tst_manual_redirect_with_partial_body_read(self):
        s = requests.Session()
        r1 = yield from s.get(httpbin('redirect/2'), allow_redirects=False, stream=True)
        assert r1.is_redirect
        rg = yield from s.resolve_redirects(r1, r1.request, stream=True)

        # read only the first eight bytes of the response body,
        # then follow the redirect
        r1.iter_content(8)
        r2 = next(rg)
        assert r2.is_redirect

        # read all of the response via iter_content,
        # then follow the redirect
        for _ in r2.iter_content():
            pass
        r3 = next(rg)
        assert not r3.is_redirect

    def _patch_adapter_gzipped_redirect(self, session, url):
        adapter = session.get_adapter(url=url)
        org_build_response = adapter.build_response
        self._patched_response = False

        def build_response(*args, **kwargs):
            resp = org_build_response(*args, **kwargs)
            if not self._patched_response:
                resp.raw.headers['content-encoding'] = 'gzip'
                self._patched_response = True
            return resp

        adapter.build_response = build_response

    @async_test
    def test_redirect_with_wrong_gzipped_header(self):
        s = requests.Session()
        url = httpbin('redirect/1')
        self._patch_adapter_gzipped_redirect(s, url)
        yield from s.get(url)

    def test_basic_auth_str_is_always_native(self):
        s = _basic_auth_str("test", "test")
        assert isinstance(s, builtin_str)
        assert s == "Basic dGVzdDp0ZXN0"

    @async_test
    def test_requests_history_is_saved(self):
        r = yield from requests.get('https://httpbin.org/redirect/5')
        total = r.history[-1].history
        i = 0
        for item in r.history:
            assert item.history == total[0:i]
            i=i+1

    @async_test
    def test_json_param_post_content_type_works(self):
        r = yield from requests.post(
            httpbin('post'),
            json={'life': 42}
        )
        assert r.status_code == 200
        assert 'application/json' in r.request.headers['Content-Type']
        rJson = yield from r.json()
        assert {'life': 42} == rJson['json']


class TestContentEncodingDetection(unittest.TestCase):

    def test_none(self):
        encodings = requests.utils.get_encodings_from_content('')
        assert not len(encodings)

    def test_html_charset(self):
        """HTML5 meta charset attribute"""
        content = '<meta charset="UTF-8">'
        encodings = requests.utils.get_encodings_from_content(content)
        assert len(encodings) == 1
        assert encodings[0] == 'UTF-8'

    def test_html4_pragma(self):
        """HTML4 pragma directive"""
        content = '<meta http-equiv="Content-type" content="text/html;charset=UTF-8">'
        encodings = requests.utils.get_encodings_from_content(content)
        assert len(encodings) == 1
        assert encodings[0] == 'UTF-8'

    def test_xhtml_pragma(self):
        """XHTML 1.x served with text/html MIME type"""
        content = '<meta http-equiv="Content-type" content="text/html;charset=UTF-8" />'
        encodings = requests.utils.get_encodings_from_content(content)
        assert len(encodings) == 1
        assert encodings[0] == 'UTF-8'

    def test_xml(self):
        """XHTML 1.x served as XML"""
        content = '<?xml version="1.0" encoding="UTF-8"?>'
        encodings = requests.utils.get_encodings_from_content(content)
        assert len(encodings) == 1
        assert encodings[0] == 'UTF-8'

    def test_precedence(self):
        content = '''
        <?xml version="1.0" encoding="XML"?>
        <meta charset="HTML5">
        <meta http-equiv="Content-type" content="text/html;charset=HTML4" />
        '''.strip()
        encodings = requests.utils.get_encodings_from_content(content)
        assert encodings == ['HTML5', 'HTML4', 'XML']


class TestCaseInsensitiveDict(unittest.TestCase):

    def test_mapping_init(self):
        cid = CaseInsensitiveDict({'Foo': 'foo', 'BAr': 'bar'})
        assert len(cid) == 2
        assert 'foo' in cid
        assert 'bar' in cid

    def test_iterable_init(self):
        cid = CaseInsensitiveDict([('Foo', 'foo'), ('BAr', 'bar')])
        assert len(cid) == 2
        assert 'foo' in cid
        assert 'bar' in cid

    def test_kwargs_init(self):
        cid = CaseInsensitiveDict(FOO='foo', BAr='bar')
        assert len(cid) == 2
        assert 'foo' in cid
        assert 'bar' in cid

    def test_docstring_example(self):
        cid = CaseInsensitiveDict()
        cid['Accept'] = 'application/json'
        assert cid['aCCEPT'] == 'application/json'
        assert list(cid) == ['Accept']

    def test_len(self):
        cid = CaseInsensitiveDict({'a': 'a', 'b': 'b'})
        cid['A'] = 'a'
        assert len(cid) == 2

    def test_getitem(self):
        cid = CaseInsensitiveDict({'Spam': 'blueval'})
        assert cid['spam'] == 'blueval'
        assert cid['SPAM'] == 'blueval'

    def test_fixes_649(self):
        """__setitem__ should behave case-insensitively."""
        cid = CaseInsensitiveDict()
        cid['spam'] = 'oneval'
        cid['Spam'] = 'twoval'
        cid['sPAM'] = 'redval'
        cid['SPAM'] = 'blueval'
        assert cid['spam'] == 'blueval'
        assert cid['SPAM'] == 'blueval'
        assert list(cid.keys()) == ['SPAM']

    def test_delitem(self):
        cid = CaseInsensitiveDict()
        cid['Spam'] = 'someval'
        del cid['sPam']
        assert 'spam' not in cid
        assert len(cid) == 0

    def test_contains(self):
        cid = CaseInsensitiveDict()
        cid['Spam'] = 'someval'
        assert 'Spam' in cid
        assert 'spam' in cid
        assert 'SPAM' in cid
        assert 'sPam' in cid
        assert 'notspam' not in cid

    def test_get(self):
        cid = CaseInsensitiveDict()
        cid['spam'] = 'oneval'
        cid['SPAM'] = 'blueval'
        assert cid.get('spam') == 'blueval'
        assert cid.get('SPAM') == 'blueval'
        assert cid.get('sPam') == 'blueval'
        assert cid.get('notspam', 'default') == 'default'

    def test_update(self):
        cid = CaseInsensitiveDict()
        cid['spam'] = 'blueval'
        cid.update({'sPam': 'notblueval'})
        assert cid['spam'] == 'notblueval'
        cid = CaseInsensitiveDict({'Foo': 'foo', 'BAr': 'bar'})
        cid.update({'fOO': 'anotherfoo', 'bAR': 'anotherbar'})
        assert len(cid) == 2
        assert cid['foo'] == 'anotherfoo'
        assert cid['bar'] == 'anotherbar'

    def test_update_retains_unchanged(self):
        cid = CaseInsensitiveDict({'foo': 'foo', 'bar': 'bar'})
        cid.update({'foo': 'newfoo'})
        assert cid['bar'] == 'bar'

    def test_iter(self):
        cid = CaseInsensitiveDict({'Spam': 'spam', 'Eggs': 'eggs'})
        keys = frozenset(['Spam', 'Eggs'])
        assert frozenset(iter(cid)) == keys

    def test_equality(self):
        cid = CaseInsensitiveDict({'SPAM': 'blueval', 'Eggs': 'redval'})
        othercid = CaseInsensitiveDict({'spam': 'blueval', 'eggs': 'redval'})
        assert cid == othercid
        del othercid['spam']
        assert cid != othercid
        assert cid == {'spam': 'blueval', 'eggs': 'redval'}

    def test_setdefault(self):
        cid = CaseInsensitiveDict({'Spam': 'blueval'})
        assert cid.setdefault('spam', 'notblueval') == 'blueval'
        assert cid.setdefault('notspam', 'notblueval') == 'notblueval'

    def test_lower_items(self):
        cid = CaseInsensitiveDict({
            'Accept': 'application/json',
            'user-Agent': 'requests',
        })
        keyset = frozenset(lowerkey for lowerkey, v in cid.lower_items())
        lowerkeyset = frozenset(['accept', 'user-agent'])
        assert keyset == lowerkeyset

    def test_preserve_key_case(self):
        cid = CaseInsensitiveDict({
            'Accept': 'application/json',
            'user-Agent': 'requests',
        })
        keyset = frozenset(['Accept', 'user-Agent'])
        assert frozenset(i[0] for i in cid.items()) == keyset
        assert frozenset(cid.keys()) == keyset
        assert frozenset(cid) == keyset

    def test_preserve_last_key_case(self):
        cid = CaseInsensitiveDict({
            'Accept': 'application/json',
            'user-Agent': 'requests',
        })
        cid.update({'ACCEPT': 'application/json'})
        cid['USER-AGENT'] = 'requests'
        keyset = frozenset(['ACCEPT', 'USER-AGENT'])
        assert frozenset(i[0] for i in cid.items()) == keyset
        assert frozenset(cid.keys()) == keyset
        assert frozenset(cid) == keyset


class UtilsTestCase(unittest.TestCase):

    def test_super_len_io_streams(self):
        """ Ensures that we properly deal with different kinds of IO streams. """
        # uses StringIO or io.StringIO (see import above)
        from io import BytesIO
        from requests.utils import super_len

        assert super_len(StringIO.StringIO()) == 0
        assert super_len(
            StringIO.StringIO('with so much drama in the LBC')) == 29

        assert super_len(BytesIO()) == 0
        assert super_len(
            BytesIO(b"it's kinda hard bein' snoop d-o-double-g")) == 40

        try:
            import cStringIO
        except ImportError:
            pass
        else:
            assert super_len(
                cStringIO.StringIO('but some how, some way...')) == 25

    def test_get_environ_proxies_ip_ranges(self):
        """Ensures that IP addresses are correctly matches with ranges
        in no_proxy variable."""
        from requests.utils import get_environ_proxies
        os.environ['no_proxy'] = "192.168.0.0/24,127.0.0.1,localhost.localdomain,172.16.1.1"
        assert get_environ_proxies('http://192.168.0.1:5000/') == {}
        assert get_environ_proxies('http://192.168.0.1/') == {}
        assert get_environ_proxies('http://172.16.1.1/') == {}
        assert get_environ_proxies('http://172.16.1.1:5000/') == {}
        assert get_environ_proxies('http://192.168.1.1:5000/') != {}
        assert get_environ_proxies('http://192.168.1.1/') != {}

    def test_get_environ_proxies(self):
        """Ensures that IP addresses are correctly matches with ranges
        in no_proxy variable."""
        from requests.utils import get_environ_proxies
        os.environ['no_proxy'] = "127.0.0.1,localhost.localdomain,192.168.0.0/24,172.16.1.1"
        assert get_environ_proxies(
            'http://localhost.localdomain:5000/v1.0/') == {}
        assert get_environ_proxies('http://www.requests.com/') != {}

    def test_is_ipv4_address(self):
        from requests.utils import is_ipv4_address
        assert is_ipv4_address('8.8.8.8')
        assert not is_ipv4_address('8.8.8.8.8')
        assert not is_ipv4_address('localhost.localdomain')

    def test_is_valid_cidr(self):
        from requests.utils import is_valid_cidr
        assert not is_valid_cidr('8.8.8.8')
        assert is_valid_cidr('192.168.1.0/24')

    def test_dotted_netmask(self):
        from requests.utils import dotted_netmask
        assert dotted_netmask(8) == '255.0.0.0'
        assert dotted_netmask(24) == '255.255.255.0'
        assert dotted_netmask(25) == '255.255.255.128'

    def test_address_in_network(self):
        from requests.utils import address_in_network
        assert address_in_network('192.168.1.1', '192.168.1.0/24')
        assert not address_in_network('172.16.0.1', '192.168.1.0/24')

    def test_get_auth_from_url(self):
        """Ensures that username and password in well-encoded URI as per
        RFC 3986 are correclty extracted."""
        from requests.utils import get_auth_from_url
        from requests.compat import quote
        percent_encoding_test_chars = "%!*'();:@&=+$,/?#[] "
        url_address = "request.com/url.html#test"
        url = "http://" + quote(
            percent_encoding_test_chars, '') + ':' + quote(
            percent_encoding_test_chars, '') + '@' + url_address
        (username, password) = get_auth_from_url(url)
        assert username == percent_encoding_test_chars
        assert password == percent_encoding_test_chars


class TestMorselToCookieExpires(unittest.TestCase):

    """Tests for morsel_to_cookie when morsel contains expires."""

    def test_expires_valid_str(self):
        """Test case where we convert expires from string time."""

        morsel = Morsel()
        morsel['expires'] = 'Thu, 01-Jan-1970 00:00:01 GMT'
        cookie = morsel_to_cookie(morsel)
        assert cookie.expires == 1

    def test_expires_invalid_int(self):
        """Test case where an invalid type is passed for expires."""

        morsel = Morsel()
        morsel['expires'] = 100
        with pytest.raises(TypeError):
            morsel_to_cookie(morsel)

    def test_expires_invalid_str(self):
        """Test case where an invalid string is input."""

        morsel = Morsel()
        morsel['expires'] = 'woops'
        with pytest.raises(ValueError):
            morsel_to_cookie(morsel)

    def test_expires_none(self):
        """Test case where expires is None."""

        morsel = Morsel()
        morsel['expires'] = None
        cookie = morsel_to_cookie(morsel)
        assert cookie.expires is None


class TestMorselToCookieMaxAge(unittest.TestCase):

    """Tests for morsel_to_cookie when morsel contains max-age."""

    def test_max_age_valid_int(self):
        """Test case where a valid max age in seconds is passed."""

        morsel = Morsel()
        morsel['max-age'] = 60
        cookie = morsel_to_cookie(morsel)
        assert isinstance(cookie.expires, int)

    def test_max_age_invalid_str(self):
        """Test case where a invalid max age is passed."""

        morsel = Morsel()
        morsel['max-age'] = 'woops'
        with pytest.raises(TypeError):
            morsel_to_cookie(morsel)


class TestTimeout:

    @async_test
    def test_stream_timeout(self):
        try:
            yield from requests.get('https://httpbin.org/delay/10', timeout=2.0)
        except requests.exceptions.Timeout as e:
            assert 'Read timed out' in e.args[0].args[0]

    @async_test
    def test_invalid_timeout(self):
        with pytest.raises(ValueError) as e:
            yield from requests.get(httpbin('get'), timeout=(3, 4, 5))
        assert '(connect, read)' in str(e)

        with pytest.raises(ValueError) as e:
            yield from requests.get(httpbin('get'), timeout="foo")
        assert 'must be an int or float' in str(e)

    @async_test
    def test_none_timeout(self):
        """ Check that you can set None as a valid timeout value.

        To actually test this behavior, we'd want to check that setting the
        timeout to None actually lets the request block past the system default
        timeout. However, this would make the test suite unbearably slow.
        Instead we verify that setting the timeout to None does not prevent the
        request from succeeding.
        """
        r = yield from requests.get(httpbin('get'), timeout=None)
        assert r.status_code == 200

    @async_test
    def test_read_timeout(self):
        try:
            yield from requests.get(httpbin('delay/10'), timeout=(None, 0.1))
            assert False, "The recv() request should time out."
        except ReadTimeout:
            pass

    @async_test
    def test_connect_timeout(self):
        try:
            yield from requests.get(TARPIT, timeout=(0.1, None))
            assert False, "The connect() request should time out."
        except ConnectTimeout as e:
            assert isinstance(e, ConnectionError)
            assert isinstance(e, Timeout)

    @async_test
    def test_total_timeout_connect(self):
        try:
            yield from requests.get(TARPIT, timeout=(0.1, 0.1))
            assert False, "The connect() request should time out."
        except ConnectTimeout:
            pass


SendCall = collections.namedtuple('SendCall', ('args', 'kwargs'))


class RedirectSession(SessionRedirectMixin):
    def __init__(self, order_of_redirects):
        self.redirects = order_of_redirects
        self.calls = []
        self.max_redirects = 30
        self.cookies = {}
        self.trust_env = False

    def send(self, *args, **kwargs):
        self.calls.append(SendCall(args, kwargs))
        return self.build_response()

    def build_response(self):
        request = self.calls[-1].args[0]
        r = requests.Response()

        try:
            r.status_code = int(self.redirects.pop(0))
        except IndexError:
            r.status_code = 200

        r.headers = CaseInsensitiveDict({'Location': '/'})
        r.raw = self._build_raw()
        r.request = request
        return r

    def _build_raw(self):
        string = StringIO.StringIO('')
        setattr(string, 'release_conn', lambda *args: args)
        return string


class TestRedirects:
    default_keyword_args = {
        'stream': False,
        'verify': True,
        'cert': None,
        'timeout': None,
        'allow_redirects': False,
        'proxies': {},
    }

    @async_test
    def test_requests_are_updated_each_time(self):
        session = RedirectSession([303, 307])
        prep = requests.Request('POST', 'http://httpbin.org/post').prepare()
        r0 = yield from session.send(prep)
        assert r0.request.method == 'POST'
        assert session.calls[-1] == SendCall((r0.request,), {})
        redirect_generator = yield from session.resolve_redirects(r0, prep)
        for response in redirect_generator:
            assert response.request.method == 'GET'
            send_call = SendCall((response.request,),
                                 TestRedirects.default_keyword_args)
            assert session.calls[-1] == send_call



@pytest.fixture
def list_of_tuples():
    return [
        (('a', 'b'), ('c', 'd')),
        (('c', 'd'), ('a', 'b')),
        (('a', 'b'), ('c', 'd'), ('e', 'f')),
        ]


def test_data_argument_accepts_tuples(list_of_tuples):
    """
    Ensure that the data argument will accept tuples of strings
    and properly encode them.
    """
    for data in list_of_tuples:
        p = PreparedRequest()
        p.prepare(
            method='GET',
            url='http://www.example.com',
            data=data,
            hooks=default_hooks()
        )
        assert p.body == urlencode(data)


def assert_copy(p, p_copy):
    for attr in ('method', 'url', 'headers', '_cookies', 'body', 'hooks'):
        assert getattr(p, attr) == getattr(p_copy, attr)


def test_prepared_request_empty_copy():
    p = PreparedRequest()
    assert_copy(p, p.copy())


def test_prepared_request_no_cookies_copy():
    p = PreparedRequest()
    p.prepare(
        method='GET',
        url='http://www.example.com',
        data='foo=bar',
        hooks=default_hooks()
    )
    assert_copy(p, p.copy())


def test_prepared_request_complete_copy():
    p = PreparedRequest()
    p.prepare(
        method='GET',
        url='http://www.example.com',
        data='foo=bar',
        hooks=default_hooks(),
        cookies={'foo': 'bar'}
    )
    assert_copy(p, p.copy())

def test_prepare_unicode_url():
    p = PreparedRequest()
    p.prepare(
        method='GET',
        url=u('http://www.example.com/üniçø∂é')
    )
    assert_copy(p, p.copy())

if __name__ == '__main__':
    unittest.main()
