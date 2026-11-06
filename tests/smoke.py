#!/usr/bin/env python3
"""One complete public acceptance scenario, cumulative by lab number."""
import base64
import hashlib
import uuid
from client import Client, LAB, arguments, check, run, timestamp


def main():
    args = arguments('Messenger public smoke; creates synthetic users and messages')
    c = Client(args.base_url,args.ca_file)
    alice,bob,eve,chat = c.bootstrap()
    path = '/api/v1/chats/'+chat['id']
    c.call('POST','/api/v1/users',409,body=alice['registration'])
    c.call('GET','/api/v1/users/me',401)
    c.call('GET',path,404,actor=eve)
    me = c.call('GET','/api/v1/users/me',actor=alice,schema='User')['json']
    check(me==alice['user'],'current user mismatch')
    listed = c.call('GET','/api/v1/chats?limit=100',actor=bob,schema='ChatPage')['json']
    check(any(x['id']==chat['id'] for x in listed['items']),'member cannot list chat')
    hidden = c.call('GET','/api/v1/chats?limit=100',actor=eve,schema='ChatPage')['json']
    check(all(x['id']!=chat['id'] for x in hidden['items']),'chat leaks to outsider')
    key = uuid.uuid4().hex
    first = c.message(alice,chat,'Привет, мир 👋',key=key)['json']
    second = c.message(bob,chat,'Сообщение Боба')['json']
    check(first['sender_id']==alice['id'] and second['sender_id']==bob['id'],'sender was not derived from actor')
    check(first['chat_id']==second['chat_id']==chat['id'],'wrong chat in message')
    check(first['text']=='Привет, мир 👋' and second['text']=='Сообщение Боба','message text changed')
    page1 = c.call('GET',path+'/messages?limit=1',actor=bob,schema='MessagePage')['json']
    check(len(page1['items'])==1 and page1['next_cursor'],'first page must have one item and cursor')
    from urllib.parse import quote
    page2 = c.call('GET',path+'/messages?limit=1&cursor='+quote(page1['next_cursor'],safe=''),actor=alice,schema='MessagePage')['json']
    check(len(page2['items'])==1 and page2['next_cursor'] is None,'second page must finish history')
    ordered = sorted([first,second],key=lambda x:(timestamp(x['created_at']),uuid.UUID(x['id']).int))
    check(page1['items']+page2['items']==ordered,'pagination lost, duplicated or reordered a message')
    h = {'Idempotency-Key':uuid.uuid4().hex} if LAB>=3 else {}
    c.call('POST',path+'/messages',400,actor=alice,body={'text':''},headers=h)
    c.call('POST',path+'/messages',404,actor=eve,body={'text':'чужое'},headers=h)
    c.call('GET',path+'/messages?limit=0',400,actor=alice)
    if LAB>=2:
        bad = base64.b64encode((alice['registration']['username']+':wrong-password').encode()).decode()
        c.call('POST','/api/v1/auth/sessions',401,headers={'Authorization':'Basic '+bad})
        good = base64.b64encode((alice['registration']['username']+':'+alice['registration']['password']).encode()).decode()
        c.call('GET','/api/v1/users/me',401,headers={'Authorization':'Basic '+good})
        c.call('GET','/api/v1/users/me',401,headers={'X-User-Id':alice['id']})
        me = c.call('GET','/api/v1/users/me',actor=alice,headers={'X-User-Id':eve['id']},schema='User')['json']
        check(me['id']==alice['id'],'X-User-Id overrode bearer identity')
    if LAB>=3:
        replay = c.message(alice,chat,first['text'],key=key)['json']
        check(replay==first,'idempotency replay differs')
        c.call('POST',path+'/messages',409,actor=alice,body={'text':'другой body'},headers={'Idempotency-Key':key})
        c.call('POST',path+'/messages',400,actor=alice,body={'text':'без ключа'})
    if LAB>=4:
        att,content = c.attachment(alice,chat)
        # A queued attachment must already be linkable to its own chat.
        attached = c.message(alice,chat,'',attachments=[att['id']])['json']
        check(attached['attachment_ids']==[att['id']],'message lost attachment')
        ready = c.wait_ready(bob,att) if LAB>=5 else att
        check(ready['error_code'] is None,'ready attachment has an error')
        download = c.call('GET','/api/v1/attachments/'+att['id']+'/content',actor=bob)
        check(download['bytes']==content,'download differs from original')
        check(download['headers'].get_content_type()=='image/png','original content type mismatch')
        for suffix in ('','/content'):
            c.call('GET','/api/v1/attachments/'+att['id']+suffix,404,actor=eve)
        if LAB>=5:
            variants = [x for x in ready['variants'] if x['name']=='thumbnail']
            check(len(variants)==1,'expected exactly one thumbnail')
            v = variants[0]
            check((v['width'],v['height'])==(256,160),'thumbnail metadata has wrong dimensions')
            thumb = c.call('GET','/api/v1/attachments/'+att['id']+'/content?variant=thumbnail',actor=bob)
            check(thumb['headers'].get_content_type()=='image/jpeg','thumbnail content type mismatch')
            check(len(thumb['bytes'])==v['size_bytes'] and hashlib.sha256(thumb['bytes']).hexdigest()==v['sha256'],'thumbnail checksum mismatch')
            from image_probe import jpeg_size
            check(jpeg_size(thumb['bytes'])==(256,160),'actual JPEG dimensions mismatch')
    if LAB>=2:
        c.call('DELETE','/api/v1/auth/sessions/current',204,actor=alice)
        c.call('GET','/api/v1/users/me',401,actor=alice)
        c.call('DELETE','/api/v1/auth/sessions/current',401,actor=alice)
    print('PASS: lab%d public API scenario; infrastructure and grading evidence remain separate.' % LAB)

if __name__=='__main__':
    run(main)
