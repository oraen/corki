use super::canonical;

#[test]
fn address_admission_uses_the_parsed_host() {
    assert_eq!(
        canonical("http://0x7f000001/mcp", true),
        Ok("http://127.0.0.1/mcp".into())
    );
    assert_eq!(canonical("http://localhost./mcp", true), Err(-4));
    assert_eq!(canonical("https://[::1%25eth0]/mcp", true), Err(-1));
}

#[test]
fn canonicalization_preserves_native_special_url_rules() {
    assert_eq!(
        canonical(" HTTPS:example.test/%2e%2e/mcp ", true),
        Ok("https://example.test/mcp".into())
    );
    assert_eq!(
        canonical("https://:@example.test/mcp", true),
        Ok("https://example.test/mcp".into())
    );
}

#[test]
fn agent_policy_is_separate_from_transport_url_parsing() {
    assert_eq!(canonical("http://example.test/mcp#fragment", true), Err(-3));
    assert_eq!(
        canonical("http://example.test/mcp#fragment", false),
        Ok("http://example.test/mcp#fragment".into())
    );
}
