"""Small helper used to exercise Dispatch code review on the playground."""


def greet(name):
    return "Hello, " + name + "!"


def greet_all(names):
    result = ""
    for n in names:
        result += greet(n) + " "
    return result
