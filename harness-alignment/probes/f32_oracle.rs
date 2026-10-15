//! Compile against Codex's checksum-verified zmij 1.0.19, not Corki's formatter.
use std::io::{self, BufRead};

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.expect("input");
        let mut fields = line.split_whitespace();
        let mode = fields.next().expect("mode");
        let raw = fields.next().expect("number");
        assert!(fields.next().is_none());
        let value = match mode {
            "u" => raw.parse::<u64>().expect("u64") as f32,
            "i" => raw.parse::<i64>().expect("i64") as f32,
            "b" => f32::from_bits(raw.parse::<u32>().expect("bits")),
            _ => panic!("unknown mode"),
        };
        println!("{} {}", value.to_bits(), zmij::Buffer::new().format(value));
    }
}
