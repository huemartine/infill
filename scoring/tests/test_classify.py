

def test_acquirable_authority_waives_cmha_and_port():
    from scoring.classify import is_acquirable_authority
    cfg = {"acquirable_authorities": {"owner_keywords": [
        "METROPOLITAN HOUSING", "PORT OF GREATER", "LAND BANK"]}}
    assert is_acquirable_authority("CINCINNATI METROPOLITAN HOUSING AUTHORITY", cfg)
    assert is_acquirable_authority("PORT OF GREATER CINCINNATI DEV", cfg)
    assert not is_acquirable_authority("SMITH FAMILY LLC", cfg)
    assert not is_acquirable_authority(None, cfg)
    assert not is_acquirable_authority("ANYONE", {})   # no config -> no waiver
