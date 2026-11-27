import os
import re
import threading
import time


class SkillError(Exception):
    pass


class Symbol(str):
    pass


NIL = None
T = Symbol("t")

_TOKEN = re.compile(r"""
    (?P<ws>\s+|;[^\n]*)
  | (?P<num>\d+\.\d*(?:[eE][-+]?\d+)?|\d+(?:[eE][-+]?\d+)?)
  | (?P<str>"(?:\\.|[^"\\])*")
  | (?P<op>~>|->|==|!=|<=|>=|&&|\|\||[-+*/=<>!'(),:])
  | (?P<sym>[A-Za-z_?@$][A-Za-z0-9_?@$.]*)
""", re.X)

_BINARY = [
    ("||",), ("&&",), ("==", "!="), ("<", ">", "<=", ">="), ("+", "-"), ("*", "/"),
]


def tokenize(text):
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise SkillError("*Error* parser: unexpected character %r" % text[pos])
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group(), m.start(), m.end()))
        pos = m.end()
    return out


class Parser:
    def __init__(self, text):
        self.toks = tokenize(text)
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else (None, None, -1, -1)

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise SkillError("*Error* parser: expected %r, got %r" % (value, tok[1]))
        self.i += 1
        return tok

    def program(self):
        forms = []
        while self.peek()[0] is not None:
            forms.append(self.expr())
        return forms

    def expr(self):
        left = self.binary(0)
        if self.peek()[1] == "=" and isinstance(left, Symbol):
            self.take("=")
            return [Symbol("setq"), left, self.expr()]
        return left

    def binary(self, level):
        if level == len(_BINARY):
            return self.unary()
        left = self.binary(level + 1)
        while self.peek()[1] in _BINARY[level]:
            op = self.take()[1]
            left = [Symbol("__op__" + op), left, self.binary(level + 1)]
        return left

    def unary(self):
        tok = self.peek()
        if tok[1] == "-" and self.peek(1)[0] == "num" and self.peek(1)[2] == tok[3]:
            self.take()
            return -self.atom_number(self.take()[1])
        if tok[1] == "!":
            self.take()
            return [Symbol("null"), self.unary()]
        return self.postfix(self.primary())

    def postfix(self, node):
        while self.peek()[1] in ("~>", "->"):
            self.take()
            node = [Symbol("__getattr__"), node, Symbol(self.take()[1])]
        return node

    @staticmethod
    def atom_number(text):
        return float(text) if any(c in text for c in ".eE") else int(text)

    def primary(self):
        kind, text, start, end = self.take()
        if kind == "num":
            return self.atom_number(text)
        if kind == "str":
            return bytes(text[1:-1], "utf-8").decode("unicode_escape")
        if text == "'":
            nxt = self.peek()
            if nxt[1] == "(":
                return [Symbol("quote"), self.lisp_list(quoted=True)]
            return [Symbol("quote"), Symbol(self.take()[1])]
        if kind == "sym":
            nxt = self.peek()
            if nxt[1] == "(" and nxt[2] == end:
                self.take("(")
                if text in ("let", "prog") and self.peek()[1] == "(":
                    return [Symbol(text), self.bindings()] + self.args(")")
                return [Symbol(text)] + self.args(")")
            return T if text == "t" else (NIL if text == "nil" else Symbol(text))
        if text == "(":
            self.i -= 1
            return self.lisp_list()
        raise SkillError("*Error* parser: unexpected %r" % text)

    def bindings(self):
        self.take("(")
        out = []
        while self.peek()[1] != ")":
            if self.peek()[1] == "(":
                self.take("(")
                name = Symbol(self.take()[1])
                init = self.expr() if self.peek()[1] != ")" else None
                self.take(")")
                out.append([name, init])
            else:
                out.append(Symbol(self.take()[1]))
        self.take(")")
        return out

    def args(self, close):
        out = []
        while self.peek()[1] != close:
            if self.peek()[0] is None:
                raise SkillError("*Error* parser: unbalanced parentheses")
            if self.peek()[1] == ",":
                self.take()
                continue
            out.append(self.expr())
        self.take(close)
        return out

    def lisp_list(self, quoted=False):
        self.take("(")
        if quoted:
            items = []
            while self.peek()[1] != ")":
                items.append(self.primary() if self.peek()[1] != "(" else self.lisp_list(True))
            self.take(")")
            return items
        head = self.peek()
        if head[0] == "sym" and self.peek(1)[1] not in _ALL_OPS and head[1] not in ("t", "nil"):
            self.take()
            if head[1] in ("let", "prog") and self.peek()[1] == "(":
                return [Symbol(head[1]), self.bindings()] + self.args(")")
            return [Symbol(head[1])] + self.args(")")
        node = self.expr()
        self.take(")")
        return node


_ALL_OPS = {op for group in _BINARY for op in group} | {"=", "~>", "->"}


def format_value(v):
    if v is None or v is False:
        return "nil"
    if v is True:
        return "t"
    if isinstance(v, Symbol):
        return str(v)
    if isinstance(v, str):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "(" + " ".join(format_value(x) for x in v) + ")" if v else "nil"
    return str(v)


def _sprintf(fmt, *args):
    out, it = [], iter(args)

    def repl(m):
        spec = m.group(0)
        if spec == "%%":
            return "%"
        if spec[-1] == "L":
            return format_value(next(it))
        if spec[-1] == "n":
            return str(next(it))
        if spec[-1] in "dix":
            return (spec[:-1] + ("x" if spec[-1] == "x" else "d")) % int(next(it))
        if spec[-1] in "fge":
            return spec % float(next(it))
        val = next(it)
        return spec % (val if isinstance(val, str) else format_value(val))
    out.append(re.sub(r"%[-+ 0#]*\d*(?:\.\d+)?[sdixfgeLn%]", repl, fmt))
    return "".join(out)


class Session:
    def __init__(self, env=None, ciw=None):
        self.globals = {}
        self.env = env if env is not None else os.environ
        self.ciw = ciw if ciw is not None else []
        self.db = {}
        self.local = threading.local()

    def take_loaded(self):
        loaded = getattr(self.local, "loaded", [])
        self.local.loaded = []
        return loaded

    def eval_text(self, text):
        result = None
        for form in Parser(text).program():
            result = self.eval(form, [])
        return result

    def lookup(self, name, scopes):
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        if name in self.globals:
            return self.globals[name]
        raise SkillError("*Error* eval: unbound variable - %s" % name)

    def assign(self, name, value, scopes):
        for scope in reversed(scopes):
            if name in scope:
                scope[name] = value
                return value
        self.globals[name] = value
        return value

    def body(self, forms, scopes):
        result = None
        for f in forms:
            result = self.eval(f, scopes)
        return result

    def eval(self, node, scopes):
        if isinstance(node, Symbol):
            return T if node == "t" else self.lookup(node, scopes)
        if not isinstance(node, list):
            return node
        if not node:
            return None
        head, rest = node[0], node[1:]
        special = _SPECIAL.get(head)
        if special:
            return special(self, rest, scopes)
        args = [self.eval(a, scopes) for a in rest]
        if head.startswith("__op__"):
            return _binop(head[6:], *args)
        fn = _BUILTINS.get(head)
        if fn is None:
            proc = self.globals.get("__proc__" + head)
            if proc is None:
                raise SkillError("*Error* eval: undefined function - %s" % head)
            params, forms = proc
            return self.body(forms, scopes + [dict(zip(params, args + [None] * len(params)))])
        return fn(self, *args)


def _binop(op, a, b):
    if op == "+":
        return (a + b) if not (isinstance(a, str) or isinstance(b, str)) else _type_err(op)
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            raise SkillError("*Error* quotient: Attempted to divide by zero")
        return a // b if isinstance(a, int) and isinstance(b, int) else a / b
    if op == "==":
        return T if a == b else None
    if op == "!=":
        return T if a != b else None
    if op in ("<", ">", "<=", ">="):
        return T if {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op] else None
    raise SkillError("*Error* eval: unknown operator %s" % op)


def _type_err(op):
    raise SkillError("*Error* %s: can't handle string arguments" % op)


def _sp_progn(s, rest, scopes):
    return s.body(rest, scopes)


def _sp_quote(s, rest, scopes):
    return rest[0]


def _sp_setq(s, rest, scopes):
    return s.assign(rest[0], s.eval(rest[1], scopes), scopes)


def _sp_let(s, rest, scopes):
    frame = {}
    for spec in rest[0] if isinstance(rest[0], list) else []:
        if isinstance(spec, list):
            frame[spec[0]] = s.eval(spec[1], scopes)
        else:
            frame[spec] = None
    return s.body(rest[1:], scopes + [frame])


def _sp_if(s, rest, scopes):
    parts = [r for r in rest if r not in (Symbol("then"), Symbol("else"))]
    if "then" in [str(r) for r in rest if isinstance(r, Symbol)]:
        idx_then = [i for i, r in enumerate(rest) if r == Symbol("then")][0]
        idx_else = next((i for i, r in enumerate(rest) if r == Symbol("else")), len(rest))
        cond, yes, no = rest[0], rest[idx_then + 1:idx_else], rest[idx_else + 1:]
        return s.body(yes, scopes) if s.eval(cond, scopes) is not None else s.body(no, scopes)
    cond = parts[0]
    if s.eval(cond, scopes) is not None:
        return s.eval(parts[1], scopes) if len(parts) > 1 else None
    return s.body(parts[2:], scopes)


def _sp_when(s, rest, scopes):
    return s.body(rest[1:], scopes) if s.eval(rest[0], scopes) is not None else None


def _sp_unless(s, rest, scopes):
    return s.body(rest[1:], scopes) if s.eval(rest[0], scopes) is None else None


def _sp_and(s, rest, scopes):
    val = T
    for r in rest:
        val = s.eval(r, scopes)
        if val is None:
            return None
    return val


def _sp_or(s, rest, scopes):
    for r in rest:
        val = s.eval(r, scopes)
        if val is not None:
            return val
    return None


def errset_record(exc):
    return ["eval", 0, T, None, [str(exc)]]


def _sp_errset(s, rest, scopes):
    try:
        return [s.eval(rest[0], scopes)]
    except SkillError as exc:
        s.globals["errset.errset"] = errset_record(exc)
        return None


def _sp_lambda(s, rest, scopes):
    params = rest[0] if isinstance(rest[0], list) else ([rest[0]] if rest[0] else [])
    return ("__lambda__", [str(p) for p in params], rest[1:], list(scopes))


def apply_fn(s, fn, args):
    if isinstance(fn, tuple) and fn and fn[0] == "__lambda__":
        _, params, forms, closure = fn
        return s.body(forms, closure + [dict(zip(params, list(args) + [None] * len(params)))])
    name = str(fn)
    if name in _BUILTINS:
        return _BUILTINS[name](s, *args)
    proc = s.globals.get("__proc__" + name)
    if proc is None:
        raise SkillError("*Error* eval: undefined function - %s" % name)
    params, forms = proc
    return s.body(forms, [dict(zip(params, list(args) + [None] * len(params)))])


def _sp_foreach(s, rest, scopes):
    var, seq = rest[0], s.eval(rest[1], scopes)
    for item in seq or []:
        s.body(rest[2:], scopes + [{var: item}])
    return seq


def _sp_procedure(s, rest, scopes):
    sig = rest[0]
    s.globals["__proc__" + sig[0]] = (list(sig[1:]), rest[1:])
    return Symbol(sig[0])


def _sp_getattr(s, rest, scopes):
    obj = s.eval(rest[0], scopes)
    return obj.get(str(rest[1])) if isinstance(obj, dict) else None


_SPECIAL = {
    "progn": _sp_progn, "quote": _sp_quote, "setq": _sp_setq, "let": _sp_let, "prog": _sp_let,
    "if": _sp_if, "when": _sp_when, "unless": _sp_unless, "and": _sp_and, "or": _sp_or,
    "__op__&&": _sp_and, "__op__||": _sp_or, "errset": _sp_errset, "foreach": _sp_foreach,
    "procedure": _sp_procedure, "__getattr__": _sp_getattr, "lambda": _sp_lambda,
}


def _sleep(s, n):
    time.sleep(float(n))
    return T


def _printf(s, fmt, *args):
    s.ciw.append(_sprintf(fmt, *args))
    return T


def _evalstring(s, text):
    return s.eval_text(text)


def _get_env(s, name):
    return s.env.get(name)


def _error(s, fmt="", *args):
    raise SkillError("*Error* " + _sprintf(fmt, *args))


def _load(s, path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not hasattr(s.local, "loaded"):
        s.local.loaded = []
    s.local.loaded.append(text)
    s.eval_text(text)
    return T


def _substring(s, text, start, length=None):
    begin = start - 1 if start > 0 else len(text) + start
    return text[begin:] if length is None else text[begin:begin + length]


def _build_string(s, items, sep=" "):
    return sep.join(x if isinstance(x, str) else format_value(x) for x in items or [])


def _mapcar(s, fn, seq):
    return [apply_fn(s, fn, [x]) for x in seq or []] or None


def _nth(s, n, seq):
    return seq[n] if seq and 0 <= n < len(seq) else None


_BUILTINS = {
    "plus": lambda s, *a: sum(a), "difference": lambda s, a, b: a - b,
    "times": lambda s, a, b: a * b, "quotient": lambda s, a, b: _binop("/", a, b),
    "list": lambda s, *a: list(a) or None, "car": lambda s, x: x[0] if x else None,
    "cdr": lambda s, x: (x[1:] or None) if x else None, "cons": lambda s, a, b: [a] + (b or []),
    "length": lambda s, x: len(x or []), "nth": _nth, "reverse": lambda s, x: list(reversed(x or [])) or None,
    "append": lambda s, *a: [i for x in a for i in (x or [])] or None,
    "null": lambda s, x: T if x is None else None, "equal": lambda s, a, b: T if a == b else None,
    "strcat": lambda s, *a: "".join(a), "strlen": lambda s, x: len(x),
    "sprintf": lambda s, port, fmt, *a: _sprintf(fmt, *a), "printf": _printf,
    "println": lambda s, x: _printf(s, "%L\n", x), "lsprintf": lambda s, fmt, *a: _sprintf(fmt, *a),
    "getCurrentTime": lambda s: time.strftime("%b %d %H:%M:%S %Y"),
    "getShellEnvVar": _get_env, "sleep": _sleep, "evalstring": _evalstring,
    "atoi": lambda s, x: int(x), "intToChar": lambda s, n: chr(n), "error": _error,
    "stringp": lambda s, x: T if isinstance(x, str) and not isinstance(x, Symbol) else None,
    "listp": lambda s, x: T if x is None or isinstance(x, list) else None,
    "numberp": lambda s, x: T if isinstance(x, (int, float)) and not isinstance(x, bool) else None,
    "symbolp": lambda s, x: T if isinstance(x, Symbol) else None,
    "boundp": lambda s, x: T if str(x) in s.globals else None,
    "fboundp": lambda s, x: T if str(x) in _BUILTINS or ("__proc__" + str(x)) in s.globals else None,
    "hiFlush": lambda s: T, "getVersion": lambda s: "fake-virtuoso",
    "ipcGetPid": lambda s: os.getpid(), "getHostName": lambda s: __import__("socket").gethostname(),
    "load": _load, "substring": _substring,
    "buildString": _build_string, "mapcar": _mapcar, "apply": lambda s, f, a: apply_fn(s, f, a or []),
}


def builtin_names():
    return sorted(_BUILTINS)
