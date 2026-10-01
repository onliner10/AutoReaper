"""Small Lua lexer for nonexecuting native API preflight."""
import re

def _lua_tokens(source):
    """Yield enough Lua tokens to inspect direct ``reaper.Name(...)`` calls.

    This is intentionally a small lexer, rather than a Lua parser. It handles
    quoted and long strings plus line/block comments so examples in prose do
    not become false API references. Dynamic lookups (``reaper[name]``) and
    aliases (``local r = reaper``) stay outside this preflight's scope and
    should use an explicit capability or verified readback.
    """
    i, length = 0, len(source)
    while i < length:
        c = source[i]
        if c.isspace():
            i += 1
            continue
        if source.startswith('--', i):
            if i + 2 < length and source[i + 2] == '[':
                opener = re.match(r'\[(=*)\[', source[i + 2:])
                if opener:
                    close = ']' + opener.group(1) + ']'
                    start = i + 2 + len(opener.group(0))
                    end = source.find(close, start)
                    i = length if end < 0 else end + len(close)
                    continue
            end = source.find('\n', i + 2)
            i = length if end < 0 else end + 1
            continue
        if c in ('"', "'"):
            quote, start = c, i
            i += 1
            while i < length:
                if source[i] == '\\':
                    i += 2
                elif source[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
            yield ('string', source[start:i])
            continue
        if c == '[':
            opener = re.match(r'\[(=*)\[', source[i:])
            if opener:
                close = ']' + opener.group(1) + ']'
                start = i + len(opener.group(0))
                end = source.find(close, start)
                i = length if end < 0 else end + len(close)
                yield ('string', source[start:end] if end >= 0 else source[start:])
                continue
        match = re.match(r'[A-Za-z_][A-Za-z0-9_]*', source[i:])
        if match:
            value = match.group(0)
            yield ('name', value)
            i += len(value)
            continue
        yield ('symbol', c)
        i += 1


def _lua_literal_text(token):
    if not token or token[0] not in ('"', "'") or token[-1:] != token[0]:
        return None
    body = token[1:-1]
    body = re.sub(r'\\([0-9]{3})', lambda m: chr(int(m.group(1))), body)
    body = body.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
    body = body.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
    return body


def _referenced_reaper_apis(source):
    """Return direct, unguarded native API calls in a Lua snippet.

    A name checked with ``reaper.APIExists("Name")`` or as a function property
    (``if reaper.Name then`` / ``type(reaper.Name)``) is treated as optional
    throughout this snippet. This conservative rule supports common guarded
    extension patterns without pretending to understand arbitrary Lua control
    flow. It may let an unguarded use of the same optional name reach REAPER
    and fail at runtime; the receipt remains visible and no API call is
    invented by the preflight.
    """
    tokens = list(_lua_tokens(source))
    calls, guarded = set(), set()
    for i in range(len(tokens) - 5):
        if (tokens[i:i + 4] == [('name', 'reaper'), ('symbol', '.'), ('name', 'APIExists'), ('symbol', '(')] and
                tokens[i + 4][0] == 'string' and tokens[i + 5] == ('symbol', ')')):
            name = _lua_literal_text(tokens[i + 4][1])
            if name:
                guarded.add(name)
    # A property test is the other common Lua spelling for optional extension
    # functions. Any non-call reference is enough to mark that name guarded;
    # this deliberately favors allowing optional code over a false rejection.
    for i in range(len(tokens) - 2):
        if (tokens[i:i + 2] == [('name', 'reaper'), ('symbol', '.')]
                and tokens[i + 2][0] == 'name'
                and (i + 3 >= len(tokens) or tokens[i + 3] != ('symbol', '('))):
            guarded.add(tokens[i + 2][1])
    for i in range(len(tokens) - 3):
        if (tokens[i:i + 3] == [('name', 'reaper'), ('symbol', '.'), tokens[i + 2]] and
                tokens[i + 2][0] == 'name' and i + 3 < len(tokens) and tokens[i + 3] == ('symbol', '(')):
            name = tokens[i + 2][1]
            if name != 'APIExists':
                calls.add(name)
    return sorted(calls - guarded)


