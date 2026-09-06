# Integration tests

Integration tests may combine the graph, fake model transports, tools, and a
temporary SQLite database. External APIs must still be replaced by deterministic
fakes unless a test is explicitly marked as optional.
