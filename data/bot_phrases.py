"""
data/bot_phrases.py — Curated database of known bot phrase patterns.

These are categorized into:
  - EXACT: phrases that must match verbatim (after normalization)
  - FUZZY: phrases compared with Jaro-Winkler similarity
  - PATTERNS: regex patterns for structural markers (LLM tone signatures)

Sources: reddit archives, manual bot flagging, known spam campaigns.
Add new phrases to expand detection coverage over time.
"""

# ─── EXACT MATCH PHRASES ──────────────────────────────────────────────────────
# Verbatim or near-verbatim bot/karma-farm comments observed in the wild.
EXACT_PHRASES = [
    # Universal karma bait
    "this! 100% this",
    "underrated comment",
    "underrated advice",
    "take my upvote",
    "i didn't expect to laugh this hard",
    "this is the way",
    "this deserves more upvotes",
    "came here to say this",
    "couldn't have said it better myself",
    "absolutely this",
    "wish i could upvote this more than once",
    "so much this",
    "not all heroes wear capes",

    # Relationship/advice bait
    "communication is key in any healthy relationship",
    "at the end of the day, it's your choice",
    "you deserve better",
    "set boundaries and stick to them",
    "your feelings are valid",
    "you are not alone in this",

    # Scam / dropship openers
    "where did you get this? i need one",
    "link please! where can i buy this",
    "i got mine from",
    "look what just arrived",
    "just got mine in the mail",

    # LLM bot conversation starters
    "that's a great question",
    "great question! to answer this",
    "that's a fantastic point",
    "great insight! to add to this",
    "hope this helps!",
    "hope that clears things up",
    "happy to help!",
    "feel free to ask if you have more questions",
    "let me know if you need more information",
    "it's important to consider all perspectives",
    "it's important to remember that",
    "while both sides have valid points",
    "at the end of the day, it depends on",
    "i completely understand where you're coming from",
    "i can see why you might feel that way",
    "it's a nuanced issue",

    # Generic agree/disagree bots
    "based and redpilled",
    "facts and logic",
    "rent free in your head",
    "skill issue",
    "touch grass",
    "go outside",

    # Crypto/NFT scam bots
    "dm me for investment opportunities",
    "check my profile for more info",
    "i made $5000 last week",
    "doubled my investment in just",
    "this coin is about to moon",
    "early investors will thank me later",
    "get in before it's too late",

    # Bot self-identification (sanctioned bots that non-bots mimic)
    "i am a bot, and this action was performed automatically",
    "beep boop i'm a bot",
    "^(i am a bot)",
]

# ─── FUZZY MATCH PHRASES ─────────────────────────────────────────────────────
# Phrases used by bots with slight variation — compare with Jaro-Winkler ≥ 0.88
FUZZY_PHRASES = [
    "communication is key in any relationship",
    "you deserve so much better than this",
    "your mental health comes first",
    "make sure to take care of yourself",
    "please see a professional if you need help",
    "this is the content i come to reddit for",
    "i needed this today thank you",
    "the internet was made for this",
    "i laughed way harder than i should have",
    "hidden gem of a comment right here",
    "why does this not have more upvotes",
    "this person gets it",
    "invest in gold or silver during uncertain times",
    "the economy is about to collapse",
    "wake up and do your own research",
]

# ─── REGEX STRUCTURAL PATTERNS ───────────────────────────────────────────────
# Compiled as re.IGNORECASE patterns — match LLM tone / structural signatures
PATTERN_STRINGS = [
    # LLM diplomatic hedging
    r"while (both|all) (sides|parties|arguments?) (have|make) valid (points?|arguments?)",
    r"it[''']?s (important|crucial|essential) to (remember|consider|note) that",
    r"at the end of the day,?\s+it (depends|comes down to|varies)",
    r"i (completely )?understand where you[''']?re coming from",
    r"(that[''']?s|this is) (a )?(great|fantastic|excellent|wonderful|good) (question|point|insight|observation)",
    r"(hope|hope that) (this|that) (helps?|clears? things? up|answers? your question)",
    r"feel free to (ask|reach out|dm|message) (me )?(if|should) you (have|need)",
    r"(happy|glad) to help",
    r"let me know if you (have|need) (any )?(more|further|additional) (questions?|info|help)",

    # Scam vector patterns
    r"(dm|message|pm) me (for|if you want|to get)",
    r"(check|visit|see) my (profile|bio|page|account) for (more|the link|details)",
    r"i (made|earned|got) \$[\d,]+ (in just|last|this) (week|month|day)",
    r"(early|smart) investors? (will|are going to) (thank|regret)",
    r"(this|the) (coin|token|crypto|nft) is (about to|going to) (moon|pump|explode|skyrocket)",

    # Karma bait structural patterns
    r"(i |this )?(needed|came here for) this\.?\s*$",
    r"why (is|does) this (not have|have so few) more (up)?votes",
    r"(hidden|underrated) (gem|comment|post) (of|right here|alert)",
    r"(this|that) deserves (more|all the) (upvotes?|awards?)",

    # Boilerplate link/redirect bots
    r"(for more (info|details|information),?\s+)(visit|check out|see|go to)",
    r"link (in|is in) (the )?(bio|comments?|description|profile)",
]

