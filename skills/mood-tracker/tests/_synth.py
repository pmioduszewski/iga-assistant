"""SYNTHETIC source-app-shaped CSV — fabricated, never the user's data."""
HEADER = ("Date,Mood,Mood Key,Tags (People),Tags Key (People),"
          "Tags (Places),Tags Key (Places),Tags (Events),"
          "Tags Key (Events),Exercise,Sleep,Menstrual,Steps,"
          "Meditation,Weather,Temperature (F),Water (cups),"
          "Caffeine (mg),Alcoholic Drinks,Notes,Reflections,Takeaways")


def csv_text() -> str:
    rows = [
        HEADER,
        "2026 Sun May 17 8:39 AM,Determined,determined,By Myself,"
        "By Myself,Home,Home,Work,Work,0.0,0.0,,15.0,,Clouds,52.0,,"
        "0,,synthetic note A,,",
        "2026 Sun May 17 3:39 PM,Grateful,grateful,Family,Family,"
        "Home,Home,Parenting,Parenting,0.0,0.0,,899.0,,Clouds,63.0,,"
        "0,,synthetic note B,,",
        "2026 Sat May 16 9:00 PM,Anxious,anxious,Boss,Boss,Office,"
        "Office,Deadline,Deadline,0.0,0.0,,200.0,,Rain,50.0,,120,1,"
        "synthetic stress,,",
        "2026 Fri May 15 7:00 AM,Tired,tired,By Myself,By Myself,"
        "Home,Home,,,0.0,0.0,,10.0,,Clear,48.0,,0,,low energy,,",
        "2026 Thu May 14 6:00 PM,Zorblax,zorblax,,,,,,,0.0,0.0,,0.0,"
        ",Clear,55.0,,0,,unknown-emotion row,,",
    ]
    return "\n".join(rows) + "\n"
