fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        // Python extension modules resolve CPython stable-ABI symbols from the loading
        // interpreter. This is the same dynamic-lookup policy used by wheel builders.
        println!("cargo:rustc-link-arg=-Wl,-undefined,dynamic_lookup");
    }
}
