def test_dbg(client, tokens):
    print("\nTYPE:", type(tokens).__name__, "| VALUE head:", str(tokens)[:60])
    assert True
