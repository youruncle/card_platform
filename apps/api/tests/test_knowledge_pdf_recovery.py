def test_pdf_fields_recover_a_glued_number_without_guessing_a_name_split():
    from card_platform.knowledge_pdf_recovery import parse_drawn_table

    pages = [
        [
            ("NET 2 NET", 53, 270),
            ("NTN-10", 53, 243.2),
            ("Anthony Davis", 90.4, 243.2),
            ("Dallas Mavericks", 316, 243.2),
        ]
    ]
    rows = parse_drawn_table(
        pages,
        headings=["NET 2 NET"],
        column_edges=lambda page: [87, 310],
        name_columns=[1],
        team_column=2,
    )
    assert [(r["card_number"], r["player"], r["team"], r["section"]) for r in rows] == [
        ("NTN-10", "Anthony Davis", "Dallas Mavericks", "NET 2 NET")
    ]


def test_nbl_first_and_last_name_columns_do_not_become_player_and_team():
    from card_platform.knowledge_pdf_recovery import parse_drawn_table

    pages = [
        [
            ("Base Cards", 72, 710),
            ("2", 92.5, 670.2),
            ("Trentyn", 108.8, 670.3),
            ("Flowers", 169.1, 670.3),
            ("Adelaide 36ers", 266.4, 670.3),
            ("ROOKIE", 441.8, 669.2),
        ]
    ]
    rows = parse_drawn_table(
        pages,
        headings=["BASE CARDS"],
        column_edges=lambda page: [105, 165, 260, 440],
        name_columns=[1, 2],
        team_column=3,
    )
    assert rows[0]["player"] == "Trentyn Flowers"
    assert rows[0]["team"] == "Adelaide 36ers"
    assert rows[0]["insert"] is None


def test_drawing_fragments_in_a_card_number_are_joined_inside_the_declared_cell():
    from card_platform.knowledge_pdf_recovery import parse_drawn_table

    pages = [
        [
            ("Big City Ballers", 72, 690),
            ("BCB", 77, 662.4),
            ("-", 98, 662.4),
            ("1", 103, 662.4),
            ("Mitch", 145, 663.6),
            ("McCarron", 211, 663.6),
            ("Adelaide 36ers", 309, 663.6),
        ]
    ]
    rows = parse_drawn_table(
        pages,
        headings=["BIG CITY BALLERS"],
        column_edges=lambda page: [140, 205, 300],
        name_columns=[1, 2],
        team_column=3,
    )
    assert rows[0]["card_number"] == "BCB-1"
    assert rows[0]["player"] == "Mitch McCarron"
    assert rows[0]["section"] == "BIG CITY BALLERS"


def test_drawing_rows_without_a_verified_heading_or_subject_stay_unresolved():
    from card_platform.knowledge_pdf_recovery import parse_drawn_table

    pages = [[("FP-89", 53, 243), ("Oklahoma City Thunder", 316, 243)]]
    assert not parse_drawn_table(
        pages,
        headings=["BASE"],
        column_edges=lambda page: [87, 310],
        name_columns=[1],
        team_column=2,
    )


def test_a_declared_blank_team_stays_null_without_dropping_a_named_card():
    from card_platform.knowledge_pdf_recovery import parse_drawn_table

    pages = [
        [
            ("All-Time Greats", 72, 409),
            ("ATG-DS", 77, 385),
            ("Dave", 145, 386),
            ("Simmons", 211, 386),
        ]
    ]
    rows = parse_drawn_table(
        pages,
        headings=["ALL-TIME GREATS"],
        column_edges=lambda p: [140, 205, 300],
        name_columns=[1, 2],
        team_column=3,
        allow_empty_team=True,
    )
    assert rows[0]["player"] == "Dave Simmons" and rows[0]["team"] is None


def test_undeclared_heading_in_name_column_does_not_inherit_previous_section():
    from card_platform.knowledge_pdf_recovery import parse_drawn_table

    pages = [
        [
            ("BASE CARDS", 10, 100),
            ("1", 10, 90),
            ("Alice A", 50, 90),
            ("Team", 150, 90),
            ("NEW INSERT", 50, 80),
            ("NI-1", 10, 70),
            ("Bob B", 50, 70),
            ("Team", 150, 70),
        ]
    ]
    rows = parse_drawn_table(
        pages,
        headings=["BASE CARDS"],
        column_edges=lambda p: [40, 140],
        name_columns=[1],
        team_column=2,
    )
    assert len(rows) == 1
    rows = parse_drawn_table(
        pages,
        headings=["BASE CARDS"],
        column_edges=lambda p: [40, 140],
        name_columns=[1],
        team_column=2,
        allow_wrapped_cells=True,
        wrapped_cell_labels=["Jackson -"],
    )
    assert len(rows) == 1


def test_declared_wrapped_cells_attach_to_following_numbered_baseline():
    from card_platform.knowledge_pdf_recovery import parse_drawn_table

    pages = [
        [
            ("BASE CARDS", 10, 100),
            ("Jackson", 90, 85),
            ("-", 122, 85),
            ("54", 10, 70),
            ("Parker", 50, 70),
            ("Cartwright", 90, 70),
            ("Team", 150, 70),
        ]
    ]
    rows = parse_drawn_table(
        pages,
        headings=["BASE CARDS"],
        column_edges=lambda p: [40, 80, 140],
        name_columns=[1, 2],
        team_column=3,
        allow_wrapped_cells=True,
        wrapped_cell_labels=["Jackson -"],
    )
    assert rows[0]["player"] == "Parker Jackson-Cartwright"
