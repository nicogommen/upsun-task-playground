"""Small helper used to exercise the Dispatch [skip ai] marker on the playground."""


def farewell(name):
    return "Goodbye, " + name + "!"


def farewell_all(names):
    result = ""
    for n in names:
        result += farewell(n) + " "
    return result
