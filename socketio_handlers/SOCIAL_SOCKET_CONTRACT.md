# Social Socket Contract

Namespace: `/social`

Identity: server derives authenticated user from Flask-Login session or JWT cookie.  
Client-provided user IDs are never trusted for identity.

## Rooms

- `user:{user_id}`: private notifications and per-user state.
- `party:{party_id}`: party roster and party-level updates.
- `conv:{conversation_id}`: live chat messages/typing for conversation members.

## Client -> Server Events

### Friends

- `friends_search` `{ query }`
- `friends_request_send` `{ target_user_id }`
- `friends_request_accept` `{ request_id? , user_id? }`
- `friends_request_decline` `{ request_id? , user_id? }`
- `friends_remove` `{ friend_user_id }`
- `friends_block` `{ user_id }`

### Presence / Activity

- `presence_set` `{ status }` (`online|away`)
- `social_activity_set` `{ mode, target, label }`

### Parties

- `party_create` `{}`
- `party_invite` `{ party_id, invitee_user_id }`
- `party_invite_accept` `{ invite_id }`
- `party_invite_decline` `{ invite_id }`
- `party_leave` `{ party_id? }`
- `party_kick` `{ party_id, member_user_id }`
- `party_transfer_leader` `{ party_id, new_leader_id }`

### Chat

- `chat_list` `{}`
- `chat_open` `{ conversation_id, limit? }`
- `chat_open_dm` `{ friend_user_id }`
- `chat_send` `{ conversation_id, type, body_text?, emoji?, image_url? }`
- `chat_typing` `{ conversation_id, is_typing }`
- `chat_read` `{ conversation_id, last_read_message_id? }`
- `chat_history_before` `{ conversation_id, before_message_id, limit? }`

## Server -> Client Events

- `social_error` `{ message }`
- `friends_state` `{ friends[], pending_in[], pending_out[], blocked[] }`
- `friend_request_received` `{ request_id, from_user }`
- `presence_update` `{ user_id, status, last_seen?, activity? }`
- `party_state` `{ party, incoming_invites[] }`
- `party_invite_received` `{ invite, party_summary }`
- `chat_list` `{ conversations[] }`
- `chat_open` `{ conversation, messages[] }`
- `chat_message` `{ message }`
- `chat_typing` `{ conversation_id, user_id, is_typing }`
- `chat_unread` `{ conversation_id, unread_count }`
- `chat_history_before` `{ conversation_id, messages[] }`

## Permission Rules (Server-Enforced)

- DMs are only allowed between accepted friends.
- Chat read/send requires conversation membership.
- Party chat requires current party membership.
- Party leadership controls (`kick`, `transfer`) require leader role.
- Chat send is rate-limited and message size-validated.
- Image chat messages require validated uploaded image URLs under `/uploads/social_chat/`.
