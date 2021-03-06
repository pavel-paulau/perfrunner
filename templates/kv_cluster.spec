[clusters]
aws =
    {% for server in servers -%}
        {{server}}:kv
    {% endfor %}

[clients]
hosts =
    {% for client in clients -%}
        {{client}}
    {% endfor %}
credentials = root:couchbase

[storage]
data = /data

[credentials]
rest = Administrator:password
ssh =  root:couchbase
