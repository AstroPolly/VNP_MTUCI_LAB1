"""Own black-box tests. Run against an already running server."""
import concurrent.futures
import sys
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0, str(Path(__file__).parent / 'tests'))
from client import Client, arguments, check, run


def history(c, actor, chat, limit=17):
    result, cursor, seen = [], None, set()
    while True:
        path = '/api/v1/chats/' + chat['id'] + '/messages?limit=' + str(limit)
        if cursor is not None:
            check(cursor not in seen, 'cursor loop')
            seen.add(cursor)
            path += '&cursor=' + quote(cursor, safe='')
        page = c.call('GET', path, actor=actor, schema='MessagePage')['json']
        result.extend(page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            return result


def main():
    args = arguments('Own negative and concurrency tests')
    c = Client(args.base_url)
    alice, bob, eve, chat = c.bootstrap()
    path = '/api/v1/chats/' + chat['id']
    c.call('POST', '/api/v1/users', 409, body=alice['registration'])
    for body in ({'username': ' ', 'display_name': 'A'}, {'username': 'abc', 'display_name': ' '},
                 {'username': 123, 'display_name': 'A'}, {'username': 'abc', 'display_name': 'A', 'extra': 1}):
        c.call('POST', '/api/v1/users', 400, body=body)
    c.call('POST', '/api/v1/users', 400, raw=b'{bad', headers={'Content-Type': 'application/json'})
    for method, suffix, body in [('GET', '', None), ('GET', '/messages', None), ('POST', '/messages', {'text': 'secret'})]:
        c.call(method, path + suffix, 404, actor=eve, body=body)
        c.call(method, path + suffix, 401, body=body)
    c.call('POST', '/api/v1/chats', 401, raw=b'{bad', headers={'Content-Type': 'application/json'})
    for members in ([alice['id'], alice['id']], [bob['id']], ['00000000-0000-4000-8000-000000000000', alice['id']]):
        c.call('POST', '/api/v1/chats', 400, actor=alice, body={'title': 'bad', 'member_ids': members})
    for value in ('', ' \t\n', 'a' * 4001, 123, None, '\ud800'):
        # json.dumps ensure_ascii permits explicit malformed surrogate test.
        import json
        c.call('POST', path + '/messages', 400, actor=alice,
               raw=json.dumps({'text': value}).encode(), headers={'Content-Type': 'application/json'})
    c.call('POST', path + '/messages', 400, actor=alice, body={'text': 'ok', 'sender_id': bob['id']})
    for query in ('limit=0', 'limit=101', 'limit=1.5', 'limit=true', 'cursor=', 'cursor=bogus'):
        c.call('GET', path + '/messages?' + query, 400, actor=alice)
    sent = [c.message(alice, chat, '🙂' * 4000)['json']]
    sent += [c.message(bob, chat, f'page {i}')['json'] for i in range(22)]
    check(history(c, alice, chat, 6) == sent, 'multi-page history mismatch')
    check(history(c, alice, chat, 24) == sent, 'exact full last page did not terminate')
    page = c.call('GET', path + '/messages?limit=1', actor=alice, schema='MessagePage')['json']
    other = c.call('POST', '/api/v1/chats', 201, actor=alice,
                   body={'title': 'other', 'member_ids': [alice['id']]}, schema='Chat')['json']
    c.call('GET', '/api/v1/chats/' + other['id'] + '/messages?cursor=' + quote(page['next_cursor']), 400, actor=alice)
    print('PASS: negative cases, Unicode, access, cursor and multi-page history')

    actors = [c.user('race' + str(i)) for i in range(10)]
    race_chat = c.call('POST', '/api/v1/chats', 201, actor=actors[0],
        body={'title': 'race', 'member_ids': [a['id'] for a in actors]}, schema='Chat')['json']
    def send(i):
        return Client(args.base_url).message(actors[i % 10], race_chat, 'race_' + str(i))['json']
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        confirmed = list(pool.map(send, range(100)))  # propagates every worker exception
    received = history(c, actors[0], race_chat)
    check(len(received) == len(confirmed) == 100, 'message loss or extra records')
    check(len({m['id'] for m in received}) == 100, 'duplicate IDs')
    check({m['id']: m for m in received} == {m['id']: m for m in confirmed}, 'confirmed content differs')
    # Two concurrent registrations must not both claim the same username.
    import uuid
    registration = {'username': 'race_' + uuid.uuid4().hex[:12], 'display_name': 'Race'}
    def register(_):
        return Client(args.base_url).call('POST', '/api/v1/users', [201, 409], body=registration)['status']
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(register, range(10)))
    check(codes.count(201) == 1 and codes.count(409) == 9, 'username race')
    print('PASS: 100 confirmed = 100 unique read, 10 clients; atomic username uniqueness')


if __name__ == '__main__':
    run(main)
