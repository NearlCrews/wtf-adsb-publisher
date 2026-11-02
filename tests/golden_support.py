"""The comparison both golden tests share: a fixture of named entries against a fresh recording."""


def assert_tables_match(case, recorded, observed, **labels):
    """Each name in either mapping holds the same value in both, one subtest per name."""
    for name in sorted(set(recorded) | set(observed)):
        with case.subTest(name=name, **labels):
            case.assertEqual(observed.get(name), recorded.get(name))
