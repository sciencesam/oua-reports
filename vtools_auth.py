"""IEEE SSO sign-in for vTools, yielding the PingAccess session cookie.

Adapted from vtoolseventsanalysis/python/vToolsAuth.py
(https://opensource.ieee.org/vtoolslabs/vtoolseventsanalysis). The password
is used once to drive the real IEEE login form and is never stored."""

from requests import Session
from re import compile, search
from urllib.parse import urlparse, urlunparse
from time import sleep
#from logging import basicConfig, DEBUG
#basicConfig(level=DEBUG)

REDIRECT_LOCATION = compile(r"window\.location = '(.*?)'")
FORM_ACTION = compile(r'action="(/as/.+?)"')

def get_login_headers(username: str, password: str, expected_host: str = "services10.ieee.org"):
    '''Get authentication token headers from IEEE login credentials
    
    What it returns is not guaranteed to be the same everytime (e.g. multiple keys/key names) since the remote api may change, hence why it returns the headers instead of just a token
    '''
    
    # session to persist cookies
    ses = Session()
    # seems to make it more reliable
    # not sure why
    ses.headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
    }
    
    # initial redirect
    req = ses.get('https://events.vtools.ieee.org/tego_/authentication/sign_in?_landing=/')
    req.raise_for_status()
    
    # find the js redirect "window.location", which also technically sets something in sessionStorage, but we dont care about it
    redir = search(REDIRECT_LOCATION, req.text)
    assert redir is not None, 'failed to find window.location within sign-in redirect page'
    
    # fix the js encoded url... not sure why the ampersands are encoded
    url = redir.group(1).replace('\\/', '/').replace('\\x26', '&')
    
    # login page handled by services[0-∞].ieee.org; refuse to post the
    # password anywhere but the expected IEEE identity host
    host = urlparse(url).hostname or ''
    assert host == expected_host, f'login redirected to unexpected host {host!r}; refusing to send credentials'
    req = ses.get(url)
    req.raise_for_status()
    
    redir = search(FORM_ACTION, req.text)
    assert redir is not None, 'failed to find form action within sign-in page'
    
    # fix the url to the path it wants to take us to from the form
    url_parts = urlparse(url)._replace(path=redir.group(1), query='')
    
    url = urlunparse(url_parts)
    
    # do the scary part
    req = ses.post(url, data={
        'pf.username': username,
        'pf.pass': password,
        'pf.ok': 'clicked',
        'pf.cancel': ''
    }, headers={'Referer': 'https://events.vtools.ieee.org/'})
    req.raise_for_status()
    
    # what it redirects to...
    # technically gets something from sessionStorage to verify stuff, but we dont care about it (from before)
    
    # for now, all we need to authenticate is the PA.Global_Websession cookie (set on the redirect from the POST)
    assert 'PA.Global_Websession' in ses.cookies, 'failed to get authentication token headers'
    return {'Cookie': f"PA.Global_Websession={ses.cookies['PA.Global_Websession']}"}