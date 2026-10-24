
- Can use Multiple API sources to get Info.(For Now Arctic Shift is the only source). Reddit API or any other third party api is unreliable for always working, so need to have multiple options in case, one is not working.
- Find an actual large list of bots of different kind from reddit, the official ones and the unofficial ones which are already known to be bots. Like identified, look through posts, or other info. The fundamental idea is to create a benchmark to test my program against. And as I make changes, how it improves, so need a test dataset. How to build that.
- There is this bot called tippr, Same Happened with r2tg_bot,which is not identifyble with my program, so need to look into its specifics. What are the behavior of these bots and why is my program not being able to catch them.
- Noticed when it gives verdict Bot, the score is always 0.98. Is that a coincidence or a techinical issue. Or is there any calculation error or any other error messing with the score calculation.
- Need a recheck of network mapper and the subsequent score calculation variables, if the math is right and they are being calculated correctly.
- A Linear weighted average, won't be very accurate I guess. Maybe Keep this as a future thing, but a ML model which learns as it identifies new bot and progressively gets better at detecting them.(Not for now)
- Check if the overides(Non linear veto rules) interfere with the normal linear rules that we set, and only work in extreme cases, instead of overridin all cases and simply labelling BOT or Human on simpla analysis, without taking into the account the the weighted mean Score calculated.
- Make it a wrapper, instead of giving it a name of a user , and then it working on finding whether it is a bot or not. It needs to work dynamically on a reddit page, scans all the user on the page, look for the format "u/", and then implementing the bot identifier program on them and add a label later in front of the name , label can be Human, Bot. The brightness of the label determining the score(e.g brighter red color means BOT, HIgh score. green bright means HUman score less). Ok So How to find the names on the screen, cause there are no u/. So look for normal format that user names have. SHould be easy to differentiate them from rest of text. About the permission to scan the page, the application can work as an extension or any in system program having permission. Would be better as an extension as then it won't need to be given system wide screen scanning access.(So for final product, extension seems the best option).
__________________________________
_______________

Phase - 2

-  Are we using the subreddits of the user(if too random, maybe a implication of being a bot).
- Can we store all the users, it checks on a web page. Like their actions , and behaviours. Store all of them and learn from them to improve upon itself(again moving to the ML model direction, so skip for now)
- Need to Work on Latency, it's really slow for now. What can be done for that. How to display the numbers on the page instantly, for now it takes 10 or more seconds.(We first check whether the bot is in any curated db of bots, through Community Consensus & Watchlists). Can we make it a O(1) operation. Or store all these watchlist before hand so we just need to check with our dbs, or dynamically checking would be faster?
- If comment history is hidden, does the program atleast take into account the comment posted by that user on the current page? Cause that is visible. 
- Are we implementing the 3-Tier Evidence Hierarchy

___________________________

Phase - 3
- Can we find comments for a user that has hidden comments and posts using rosint or some other API are whatever APis we are already using.
- The weighing of the different factors taken need to be dynamic. For a user  which has random(totally human like) comments, other factors like when was the account active, when not, the subreddits and name factors become more important. So The weighting of : Metadata Extractor Temporal Analyzer (S_tempora(, Text Forensics (S_linguistic),Network Mapper (S_network). Need to be dynamic and take account of different factors, for bots which does not work the usual way(might be even a human or a group spamming from many account. I want them to be counted as bots too), for that consider factors like when was the bot active, or what subs it is active in , like if focused on just 1 type of subs. Or Being Random, or maybe for bots focused on special thing like political propogandas. Need to address all these type of issues.

__________________

Phase - 4
- The Hover menu needs to have a better description of why the user's Score. On What basis it was given, a more descriptive and more specific message.
- Latency still is a issue. The scores appear quite late on a page. Maybe a network issue but still want to check if something can be done.


__________________

Phase - 5
- Corrected Dormancy Gap False Positives: Fixed sample truncation artifact where fetching only recent comments (e.g. 20-30 days) incorrectly marked older accounts as having multi-year dormant gaps.
- Velocity-Coupled Dormancy: An inactive or dormant account is only scored as a sleeper bot if its reactivation exhibits abnormal burst velocity (>=8 comments/day) or template spam, distinguishing normal human lurkers from automated bot fleets.
- Username Pattern Recalibration: Re-calibrated default Reddit-suggested usernames (Adjective-Noun-1234) from 0.85 down to 0.20 (weak prior) since millions of genuine mobile/OAuth humans use them, while custom handles are scored at 0.0 with organic human weighting.
- Biological Circadian Sleep Trough Confirmation: Verified sleep windows (>=6 hours of rest across circular midnight) now scale down circadian entropy and reward authentic human sleep biology.
- Raised Linguistic Decoupling Guard: Prevented clean natural comments from being decoupled unless confirmed high-confidence automation flags (superhuman velocity, 24/7 continuous operation) exist.
- Organic Human Shield: Accounts exhibiting natural vocabulary variety, biological sleep troughs, and non-burst cadences are protected by an organic dampener, dropping false positive scores on genuine human profiles from 55-60% down to under 10%.
- Expanded Network & Coordination Engine: Added external URL/shortener syndication detection, temporal proximity thread swarming (<90s clusters), and reciprocal echo-chamber detection.
