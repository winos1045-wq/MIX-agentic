"""
themes.py — SDX Agent Theme System
Ported from Claude Code's TypeScript theme definitions.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

# ── Theme names ───────────────────────────────────────────────────────────────

ThemeName = Literal[
    "dark",
    "light",
    "light-daltonized",
    "dark-daltonized",
    "light-ansi",
    "dark-ansi",
]

THEME_NAMES: list[str] = [
    "dark",
    "light",
    "light-daltonized",
    "dark-daltonized",
    "light-ansi",
    "dark-ansi",
]

THEME_SETTINGS: list[str] = ["auto"] + THEME_NAMES


# ── Theme dataclass ───────────────────────────────────────────────────────────

@dataclass
class Theme:
    # Core UI
    auto_accept:                          str
    bash_border:                          str
    claude:                               str
    claude_shimmer:                       str
    claude_blue_for_system_spinner:       str
    claude_blue_shimmer_for_system_spinner: str
    permission:                           str
    permission_shimmer:                   str
    plan_mode:                            str
    ide:                                  str
    prompt_border:                        str
    prompt_border_shimmer:                str
    text:                                 str
    inverse_text:                         str
    inactive:                             str
    inactive_shimmer:                     str
    subtle:                               str
    suggestion:                           str
    remember:                             str
    background:                           str
    # Semantic
    success:                              str
    error:                                str
    warning:                              str
    merged:                               str
    warning_shimmer:                      str
    # Diff
    diff_added:                           str
    diff_removed:                         str
    diff_added_dimmed:                    str
    diff_removed_dimmed:                  str
    diff_added_word:                      str
    diff_removed_word:                    str
    # Sub-agent colors
    red_for_subagents:                    str
    blue_for_subagents:                   str
    green_for_subagents:                  str
    yellow_for_subagents:                 str
    purple_for_subagents:                 str
    orange_for_subagents:                 str
    pink_for_subagents:                   str
    cyan_for_subagents:                   str
    # Misc
    professional_blue:                    str
    chrome_yellow:                        str
    # TUI V2
    clawd_body:                           str
    clawd_background:                     str
    user_message_background:              str
    user_message_background_hover:        str
    message_actions_background:           str
    selection_bg:                         str
    bash_message_background_color:        str
    memory_background_color:              str
    rate_limit_fill:                      str
    rate_limit_empty:                     str
    fast_mode:                            str
    fast_mode_shimmer:                    str
    brief_label_you:                      str
    brief_label_claude:                   str
    # Rainbow
    rainbow_red:                          str
    rainbow_orange:                       str
    rainbow_yellow:                       str
    rainbow_green:                        str
    rainbow_blue:                         str
    rainbow_indigo:                       str
    rainbow_violet:                       str
    rainbow_red_shimmer:                  str
    rainbow_orange_shimmer:               str
    rainbow_yellow_shimmer:               str
    rainbow_green_shimmer:                str
    rainbow_blue_shimmer:                 str
    rainbow_indigo_shimmer:               str
    rainbow_violet_shimmer:               str

    # ── Rich markup helper ────────────────────────────────────────────────────

    def markup(self, color_attr: str) -> str:
        """
        Return a Rich-compatible color string for use in markup tags.

        Usage:
            theme.markup("success")          → "rgb(44,122,57)"
            f"[{theme.markup('error')}]text" → "[rgb(171,43,63)]text"
        """
        color = getattr(self, color_attr, "#ffffff")
        # ANSI colors are not valid Rich markup — map to closest hex
        if color.startswith("ansi:"):
            return _ANSI_TO_HEX.get(color, "#aaaaaa")
        return color

    def style(self, color_attr: str) -> str:
        """Convenience — same as markup(), reads naturally in f-strings."""
        return self.markup(color_attr)


# ── ANSI → hex fallback table (for Rich rendering) ───────────────────────────

_ANSI_TO_HEX: dict[str, str] = {
    "ansi:black":         "#000000",
    "ansi:red":           "#cc0000",
    "ansi:green":         "#00cc00",
    "ansi:yellow":        "#cccc00",
    "ansi:blue":          "#0000cc",
    "ansi:magenta":       "#cc00cc",
    "ansi:cyan":          "#00cccc",
    "ansi:white":         "#cccccc",
    "ansi:blackBright":   "#555555",
    "ansi:redBright":     "#ff5555",
    "ansi:greenBright":   "#55ff55",
    "ansi:yellowBright":  "#ffff55",
    "ansi:blueBright":    "#5555ff",
    "ansi:magentaBright": "#ff55ff",
    "ansi:cyanBright":    "#55ffff",
    "ansi:whiteBright":   "#ffffff",
}


# ── Theme definitions ─────────────────────────────────────────────────────────

DARK = Theme(
    auto_accept                           = "rgb(175,135,255)",
    bash_border                           = "rgb(253,93,177)",
    claude                                = "rgb(215,119,87)",
    claude_shimmer                        = "rgb(235,159,127)",
    claude_blue_for_system_spinner        = "rgb(147,165,255)",
    claude_blue_shimmer_for_system_spinner= "rgb(177,195,255)",
    permission                            = "rgb(177,185,249)",
    permission_shimmer                    = "rgb(207,215,255)",
    plan_mode                             = "rgb(72,150,140)",
    ide                                   = "rgb(71,130,200)",
    prompt_border                         = "rgb(136,136,136)",
    prompt_border_shimmer                 = "rgb(166,166,166)",
    text                                  = "rgb(255,255,255)",
    inverse_text                          = "rgb(0,0,0)",
    inactive                              = "rgb(153,153,153)",
    inactive_shimmer                      = "rgb(193,193,193)",
    subtle                                = "rgb(80,80,80)",
    suggestion                            = "rgb(177,185,249)",
    remember                              = "rgb(177,185,249)",
    background                            = "rgb(0,204,204)",
    success                               = "rgb(78,186,101)",
    error                                 = "rgb(255,107,128)",
    warning                               = "rgb(255,193,7)",
    merged                                = "rgb(175,135,255)",
    warning_shimmer                       = "rgb(255,223,57)",
    diff_added                            = "rgb(34,92,43)",
    diff_removed                          = "rgb(122,41,54)",
    diff_added_dimmed                     = "rgb(71,88,74)",
    diff_removed_dimmed                   = "rgb(105,72,77)",
    diff_added_word                       = "rgb(56,166,96)",
    diff_removed_word                     = "rgb(179,89,107)",
    red_for_subagents                     = "rgb(220,38,38)",
    blue_for_subagents                    = "rgb(37,99,235)",
    green_for_subagents                   = "rgb(22,163,74)",
    yellow_for_subagents                  = "rgb(202,138,4)",
    purple_for_subagents                  = "rgb(147,51,234)",
    orange_for_subagents                  = "rgb(234,88,12)",
    pink_for_subagents                    = "rgb(219,39,119)",
    cyan_for_subagents                    = "rgb(8,145,178)",
    professional_blue                     = "rgb(106,155,204)",
    chrome_yellow                         = "rgb(251,188,4)",
    clawd_body                            = "rgb(215,119,87)",
    clawd_background                      = "rgb(0,0,0)",
    user_message_background               = "rgb(55,55,55)",
    user_message_background_hover         = "rgb(70,70,70)",
    message_actions_background            = "rgb(44,50,62)",
    selection_bg                          = "rgb(38,79,120)",
    bash_message_background_color         = "rgb(65,60,65)",
    memory_background_color               = "rgb(55,65,70)",
    rate_limit_fill                       = "rgb(177,185,249)",
    rate_limit_empty                      = "rgb(80,83,112)",
    fast_mode                             = "rgb(255,120,20)",
    fast_mode_shimmer                     = "rgb(255,165,70)",
    brief_label_you                       = "rgb(122,180,232)",
    brief_label_claude                    = "rgb(215,119,87)",
    rainbow_red                           = "rgb(235,95,87)",
    rainbow_orange                        = "rgb(245,139,87)",
    rainbow_yellow                        = "rgb(250,195,95)",
    rainbow_green                         = "rgb(145,200,130)",
    rainbow_blue                          = "rgb(130,170,220)",
    rainbow_indigo                        = "rgb(155,130,200)",
    rainbow_violet                        = "rgb(200,130,180)",
    rainbow_red_shimmer                   = "rgb(250,155,147)",
    rainbow_orange_shimmer                = "rgb(255,185,137)",
    rainbow_yellow_shimmer                = "rgb(255,225,155)",
    rainbow_green_shimmer                 = "rgb(185,230,180)",
    rainbow_blue_shimmer                  = "rgb(180,205,240)",
    rainbow_indigo_shimmer                = "rgb(195,180,230)",
    rainbow_violet_shimmer                = "rgb(230,180,210)",
)

LIGHT = Theme(
    auto_accept                           = "rgb(135,0,255)",
    bash_border                           = "rgb(255,0,135)",
    claude                                = "rgb(215,119,87)",
    claude_shimmer                        = "rgb(245,149,117)",
    claude_blue_for_system_spinner        = "rgb(87,105,247)",
    claude_blue_shimmer_for_system_spinner= "rgb(117,135,255)",
    permission                            = "rgb(87,105,247)",
    permission_shimmer                    = "rgb(137,155,255)",
    plan_mode                             = "rgb(0,102,102)",
    ide                                   = "rgb(71,130,200)",
    prompt_border                         = "rgb(153,153,153)",
    prompt_border_shimmer                 = "rgb(183,183,183)",
    text                                  = "rgb(0,0,0)",
    inverse_text                          = "rgb(255,255,255)",
    inactive                              = "rgb(102,102,102)",
    inactive_shimmer                      = "rgb(142,142,142)",
    subtle                                = "rgb(175,175,175)",
    suggestion                            = "rgb(87,105,247)",
    remember                              = "rgb(0,0,255)",
    background                            = "rgb(0,153,153)",
    success                               = "rgb(44,122,57)",
    error                                 = "rgb(171,43,63)",
    warning                               = "rgb(150,108,30)",
    merged                                = "rgb(135,0,255)",
    warning_shimmer                       = "rgb(200,158,80)",
    diff_added                            = "rgb(105,219,124)",
    diff_removed                          = "rgb(255,168,180)",
    diff_added_dimmed                     = "rgb(199,225,203)",
    diff_removed_dimmed                   = "rgb(253,210,216)",
    diff_added_word                       = "rgb(47,157,68)",
    diff_removed_word                     = "rgb(209,69,75)",
    red_for_subagents                     = "rgb(220,38,38)",
    blue_for_subagents                    = "rgb(37,99,235)",
    green_for_subagents                   = "rgb(22,163,74)",
    yellow_for_subagents                  = "rgb(202,138,4)",
    purple_for_subagents                  = "rgb(147,51,234)",
    orange_for_subagents                  = "rgb(234,88,12)",
    pink_for_subagents                    = "rgb(219,39,119)",
    cyan_for_subagents                    = "rgb(8,145,178)",
    professional_blue                     = "rgb(106,155,204)",
    chrome_yellow                         = "rgb(251,188,4)",
    clawd_body                            = "rgb(215,119,87)",
    clawd_background                      = "rgb(0,0,0)",
    user_message_background               = "rgb(240,240,240)",
    user_message_background_hover         = "rgb(252,252,252)",
    message_actions_background            = "rgb(232,236,244)",
    selection_bg                          = "rgb(180,213,255)",
    bash_message_background_color         = "rgb(250,245,250)",
    memory_background_color               = "rgb(230,245,250)",
    rate_limit_fill                       = "rgb(87,105,247)",
    rate_limit_empty                      = "rgb(39,47,111)",
    fast_mode                             = "rgb(255,106,0)",
    fast_mode_shimmer                     = "rgb(255,150,50)",
    brief_label_you                       = "rgb(37,99,235)",
    brief_label_claude                    = "rgb(215,119,87)",
    rainbow_red                           = "rgb(235,95,87)",
    rainbow_orange                        = "rgb(245,139,87)",
    rainbow_yellow                        = "rgb(250,195,95)",
    rainbow_green                         = "rgb(145,200,130)",
    rainbow_blue                          = "rgb(130,170,220)",
    rainbow_indigo                        = "rgb(155,130,200)",
    rainbow_violet                        = "rgb(200,130,180)",
    rainbow_red_shimmer                   = "rgb(250,155,147)",
    rainbow_orange_shimmer                = "rgb(255,185,137)",
    rainbow_yellow_shimmer                = "rgb(255,225,155)",
    rainbow_green_shimmer                 = "rgb(185,230,180)",
    rainbow_blue_shimmer                  = "rgb(180,205,240)",
    rainbow_indigo_shimmer                = "rgb(195,180,230)",
    rainbow_violet_shimmer                = "rgb(230,180,210)",
)

LIGHT_DALTONIZED = Theme(
    auto_accept                           = "rgb(135,0,255)",
    bash_border                           = "rgb(0,102,204)",
    claude                                = "rgb(255,153,51)",
    claude_shimmer                        = "rgb(255,183,101)",
    claude_blue_for_system_spinner        = "rgb(51,102,255)",
    claude_blue_shimmer_for_system_spinner= "rgb(101,152,255)",
    permission                            = "rgb(51,102,255)",
    permission_shimmer                    = "rgb(101,152,255)",
    plan_mode                             = "rgb(51,102,102)",
    ide                                   = "rgb(71,130,200)",
    prompt_border                         = "rgb(153,153,153)",
    prompt_border_shimmer                 = "rgb(183,183,183)",
    text                                  = "rgb(0,0,0)",
    inverse_text                          = "rgb(255,255,255)",
    inactive                              = "rgb(102,102,102)",
    inactive_shimmer                      = "rgb(142,142,142)",
    subtle                                = "rgb(175,175,175)",
    suggestion                            = "rgb(51,102,255)",
    remember                              = "rgb(51,102,255)",
    background                            = "rgb(0,153,153)",
    success                               = "rgb(0,102,153)",
    error                                 = "rgb(204,0,0)",
    warning                               = "rgb(255,153,0)",
    merged                                = "rgb(135,0,255)",
    warning_shimmer                       = "rgb(255,183,50)",
    diff_added                            = "rgb(153,204,255)",
    diff_removed                          = "rgb(255,204,204)",
    diff_added_dimmed                     = "rgb(209,231,253)",
    diff_removed_dimmed                   = "rgb(255,233,233)",
    diff_added_word                       = "rgb(51,102,204)",
    diff_removed_word                     = "rgb(153,51,51)",
    red_for_subagents                     = "rgb(204,0,0)",
    blue_for_subagents                    = "rgb(0,102,204)",
    green_for_subagents                   = "rgb(0,204,0)",
    yellow_for_subagents                  = "rgb(255,204,0)",
    purple_for_subagents                  = "rgb(128,0,128)",
    orange_for_subagents                  = "rgb(255,128,0)",
    pink_for_subagents                    = "rgb(255,102,178)",
    cyan_for_subagents                    = "rgb(0,178,178)",
    professional_blue                     = "rgb(106,155,204)",
    chrome_yellow                         = "rgb(251,188,4)",
    clawd_body                            = "rgb(215,119,87)",
    clawd_background                      = "rgb(0,0,0)",
    user_message_background               = "rgb(220,220,220)",
    user_message_background_hover         = "rgb(232,232,232)",
    message_actions_background            = "rgb(210,216,226)",
    selection_bg                          = "rgb(180,213,255)",
    bash_message_background_color         = "rgb(250,245,250)",
    memory_background_color               = "rgb(230,245,250)",
    rate_limit_fill                       = "rgb(51,102,255)",
    rate_limit_empty                      = "rgb(23,46,114)",
    fast_mode                             = "rgb(255,106,0)",
    fast_mode_shimmer                     = "rgb(255,150,50)",
    brief_label_you                       = "rgb(37,99,235)",
    brief_label_claude                    = "rgb(255,153,51)",
    rainbow_red                           = "rgb(235,95,87)",
    rainbow_orange                        = "rgb(245,139,87)",
    rainbow_yellow                        = "rgb(250,195,95)",
    rainbow_green                         = "rgb(145,200,130)",
    rainbow_blue                          = "rgb(130,170,220)",
    rainbow_indigo                        = "rgb(155,130,200)",
    rainbow_violet                        = "rgb(200,130,180)",
    rainbow_red_shimmer                   = "rgb(250,155,147)",
    rainbow_orange_shimmer                = "rgb(255,185,137)",
    rainbow_yellow_shimmer                = "rgb(255,225,155)",
    rainbow_green_shimmer                 = "rgb(185,230,180)",
    rainbow_blue_shimmer                  = "rgb(180,205,240)",
    rainbow_indigo_shimmer                = "rgb(195,180,230)",
    rainbow_violet_shimmer                = "rgb(230,180,210)",
)

DARK_DALTONIZED = Theme(
    auto_accept                           = "rgb(175,135,255)",
    bash_border                           = "rgb(51,153,255)",
    claude                                = "rgb(255,153,51)",
    claude_shimmer                        = "rgb(255,183,101)",
    claude_blue_for_system_spinner        = "rgb(153,204,255)",
    claude_blue_shimmer_for_system_spinner= "rgb(183,224,255)",
    permission                            = "rgb(153,204,255)",
    permission_shimmer                    = "rgb(183,224,255)",
    plan_mode                             = "rgb(102,153,153)",
    ide                                   = "rgb(71,130,200)",
    prompt_border                         = "rgb(136,136,136)",
    prompt_border_shimmer                 = "rgb(166,166,166)",
    text                                  = "rgb(255,255,255)",
    inverse_text                          = "rgb(0,0,0)",
    inactive                              = "rgb(153,153,153)",
    inactive_shimmer                      = "rgb(193,193,193)",
    subtle                                = "rgb(80,80,80)",
    suggestion                            = "rgb(153,204,255)",
    remember                              = "rgb(153,204,255)",
    background                            = "rgb(0,204,204)",
    success                               = "rgb(51,153,255)",
    error                                 = "rgb(255,102,102)",
    warning                               = "rgb(255,204,0)",
    merged                                = "rgb(175,135,255)",
    warning_shimmer                       = "rgb(255,234,50)",
    diff_added                            = "rgb(0,68,102)",
    diff_removed                          = "rgb(102,0,0)",
    diff_added_dimmed                     = "rgb(62,81,91)",
    diff_removed_dimmed                   = "rgb(62,44,44)",
    diff_added_word                       = "rgb(0,119,179)",
    diff_removed_word                     = "rgb(179,0,0)",
    red_for_subagents                     = "rgb(255,102,102)",
    blue_for_subagents                    = "rgb(102,178,255)",
    green_for_subagents                   = "rgb(102,255,102)",
    yellow_for_subagents                  = "rgb(255,255,102)",
    purple_for_subagents                  = "rgb(178,102,255)",
    orange_for_subagents                  = "rgb(255,178,102)",
    pink_for_subagents                    = "rgb(255,153,204)",
    cyan_for_subagents                    = "rgb(102,204,204)",
    professional_blue                     = "rgb(106,155,204)",
    chrome_yellow                         = "rgb(251,188,4)",
    clawd_body                            = "rgb(215,119,87)",
    clawd_background                      = "rgb(0,0,0)",
    user_message_background               = "rgb(55,55,55)",
    user_message_background_hover         = "rgb(70,70,70)",
    message_actions_background            = "rgb(44,50,62)",
    selection_bg                          = "rgb(38,79,120)",
    bash_message_background_color         = "rgb(65,60,65)",
    memory_background_color               = "rgb(55,65,70)",
    rate_limit_fill                       = "rgb(153,204,255)",
    rate_limit_empty                      = "rgb(69,92,115)",
    fast_mode                             = "rgb(255,120,20)",
    fast_mode_shimmer                     = "rgb(255,165,70)",
    brief_label_you                       = "rgb(122,180,232)",
    brief_label_claude                    = "rgb(255,153,51)",
    rainbow_red                           = "rgb(235,95,87)",
    rainbow_orange                        = "rgb(245,139,87)",
    rainbow_yellow                        = "rgb(250,195,95)",
    rainbow_green                         = "rgb(145,200,130)",
    rainbow_blue                          = "rgb(130,170,220)",
    rainbow_indigo                        = "rgb(155,130,200)",
    rainbow_violet                        = "rgb(200,130,180)",
    rainbow_red_shimmer                   = "rgb(250,155,147)",
    rainbow_orange_shimmer                = "rgb(255,185,137)",
    rainbow_yellow_shimmer                = "rgb(255,225,155)",
    rainbow_green_shimmer                 = "rgb(185,230,180)",
    rainbow_blue_shimmer                  = "rgb(180,205,240)",
    rainbow_indigo_shimmer                = "rgb(195,180,230)",
    rainbow_violet_shimmer                = "rgb(230,180,210)",
)

LIGHT_ANSI = Theme(
    auto_accept                           = "ansi:magenta",
    bash_border                           = "ansi:magenta",
    claude                                = "ansi:redBright",
    claude_shimmer                        = "ansi:yellowBright",
    claude_blue_for_system_spinner        = "ansi:blue",
    claude_blue_shimmer_for_system_spinner= "ansi:blueBright",
    permission                            = "ansi:blue",
    permission_shimmer                    = "ansi:blueBright",
    plan_mode                             = "ansi:cyan",
    ide                                   = "ansi:blueBright",
    prompt_border                         = "ansi:white",
    prompt_border_shimmer                 = "ansi:whiteBright",
    text                                  = "ansi:black",
    inverse_text                          = "ansi:white",
    inactive                              = "ansi:blackBright",
    inactive_shimmer                      = "ansi:white",
    subtle                                = "ansi:blackBright",
    suggestion                            = "ansi:blue",
    remember                              = "ansi:blue",
    background                            = "ansi:cyan",
    success                               = "ansi:green",
    error                                 = "ansi:red",
    warning                               = "ansi:yellow",
    merged                                = "ansi:magenta",
    warning_shimmer                       = "ansi:yellowBright",
    diff_added                            = "ansi:green",
    diff_removed                          = "ansi:red",
    diff_added_dimmed                     = "ansi:green",
    diff_removed_dimmed                   = "ansi:red",
    diff_added_word                       = "ansi:greenBright",
    diff_removed_word                     = "ansi:redBright",
    red_for_subagents                     = "ansi:red",
    blue_for_subagents                    = "ansi:blue",
    green_for_subagents                   = "ansi:green",
    yellow_for_subagents                  = "ansi:yellow",
    purple_for_subagents                  = "ansi:magenta",
    orange_for_subagents                  = "ansi:redBright",
    pink_for_subagents                    = "ansi:magentaBright",
    cyan_for_subagents                    = "ansi:cyan",
    professional_blue                     = "ansi:blueBright",
    chrome_yellow                         = "ansi:yellow",
    clawd_body                            = "ansi:redBright",
    clawd_background                      = "ansi:black",
    user_message_background               = "ansi:white",
    user_message_background_hover         = "ansi:whiteBright",
    message_actions_background            = "ansi:white",
    selection_bg                          = "ansi:cyan",
    bash_message_background_color         = "ansi:whiteBright",
    memory_background_color               = "ansi:white",
    rate_limit_fill                       = "ansi:yellow",
    rate_limit_empty                      = "ansi:black",
    fast_mode                             = "ansi:red",
    fast_mode_shimmer                     = "ansi:redBright",
    brief_label_you                       = "ansi:blue",
    brief_label_claude                    = "ansi:redBright",
    rainbow_red                           = "ansi:red",
    rainbow_orange                        = "ansi:redBright",
    rainbow_yellow                        = "ansi:yellow",
    rainbow_green                         = "ansi:green",
    rainbow_blue                          = "ansi:cyan",
    rainbow_indigo                        = "ansi:blue",
    rainbow_violet                        = "ansi:magenta",
    rainbow_red_shimmer                   = "ansi:redBright",
    rainbow_orange_shimmer                = "ansi:yellow",
    rainbow_yellow_shimmer                = "ansi:yellowBright",
    rainbow_green_shimmer                 = "ansi:greenBright",
    rainbow_blue_shimmer                  = "ansi:cyanBright",
    rainbow_indigo_shimmer                = "ansi:blueBright",
    rainbow_violet_shimmer                = "ansi:magentaBright",
)

DARK_ANSI = Theme(
    auto_accept                           = "ansi:magentaBright",
    bash_border                           = "ansi:magentaBright",
    claude                                = "ansi:redBright",
    claude_shimmer                        = "ansi:yellowBright",
    claude_blue_for_system_spinner        = "ansi:blueBright",
    claude_blue_shimmer_for_system_spinner= "ansi:blueBright",
    permission                            = "ansi:blueBright",
    permission_shimmer                    = "ansi:blueBright",
    plan_mode                             = "ansi:cyanBright",
    ide                                   = "ansi:blue",
    prompt_border                         = "ansi:white",
    prompt_border_shimmer                 = "ansi:whiteBright",
    text                                  = "ansi:whiteBright",
    inverse_text                          = "ansi:black",
    inactive                              = "ansi:white",
    inactive_shimmer                      = "ansi:whiteBright",
    subtle                                = "ansi:white",
    suggestion                            = "ansi:blueBright",
    remember                              = "ansi:blueBright",
    background                            = "ansi:cyanBright",
    success                               = "ansi:greenBright",
    error                                 = "ansi:redBright",
    warning                               = "ansi:yellowBright",
    merged                                = "ansi:magentaBright",
    warning_shimmer                       = "ansi:yellowBright",
    diff_added                            = "ansi:green",
    diff_removed                          = "ansi:red",
    diff_added_dimmed                     = "ansi:green",
    diff_removed_dimmed                   = "ansi:red",
    diff_added_word                       = "ansi:greenBright",
    diff_removed_word                     = "ansi:redBright",
    red_for_subagents                     = "ansi:redBright",
    blue_for_subagents                    = "ansi:blueBright",
    green_for_subagents                   = "ansi:greenBright",
    yellow_for_subagents                  = "ansi:yellowBright",
    purple_for_subagents                  = "ansi:magentaBright",
    orange_for_subagents                  = "ansi:redBright",
    pink_for_subagents                    = "ansi:magentaBright",
    cyan_for_subagents                    = "ansi:cyanBright",
    professional_blue                     = "rgb(106,155,204)",
    chrome_yellow                         = "ansi:yellowBright",
    clawd_body                            = "ansi:redBright",
    clawd_background                      = "ansi:black",
    user_message_background               = "ansi:blackBright",
    user_message_background_hover         = "ansi:white",
    message_actions_background            = "ansi:blackBright",
    selection_bg                          = "ansi:blue",
    bash_message_background_color         = "ansi:black",
    memory_background_color               = "ansi:blackBright",
    rate_limit_fill                       = "ansi:yellow",
    rate_limit_empty                      = "ansi:white",
    fast_mode                             = "ansi:redBright",
    fast_mode_shimmer                     = "ansi:redBright",
    brief_label_you                       = "ansi:blueBright",
    brief_label_claude                    = "ansi:redBright",
    rainbow_red                           = "ansi:red",
    rainbow_orange                        = "ansi:redBright",
    rainbow_yellow                        = "ansi:yellow",
    rainbow_green                         = "ansi:green",
    rainbow_blue                          = "ansi:cyan",
    rainbow_indigo                        = "ansi:blue",
    rainbow_violet                        = "ansi:magenta",
    rainbow_red_shimmer                   = "ansi:redBright",
    rainbow_orange_shimmer                = "ansi:yellow",
    rainbow_yellow_shimmer                = "ansi:yellowBright",
    rainbow_green_shimmer                 = "ansi:greenBright",
    rainbow_blue_shimmer                  = "ansi:cyanBright",
    rainbow_indigo_shimmer                = "ansi:blueBright",
    rainbow_violet_shimmer                = "ansi:magentaBright",
)

_THEME_MAP: dict[str, Theme] = {
    "dark":             DARK,
    "light":            LIGHT,
    "light-daltonized": LIGHT_DALTONIZED,
    "dark-daltonized":  DARK_DALTONIZED,
    "light-ansi":       LIGHT_ANSI,
    "dark-ansi":        DARK_ANSI,
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_theme(name: str) -> Theme:
    """Return a Theme by name. Falls back to DARK for unknown names."""
    return _THEME_MAP.get(name, DARK)


def resolve_theme(setting: str) -> Theme:
    """
    Resolve a theme setting (including 'auto') to a concrete Theme.
    'auto' detects the terminal background; falls back to DARK if uncertain.
    """
    if setting != "auto":
        return get_theme(setting)

    # Simple heuristic: check $COLORFGBG (set by many terminals)
    # Format: "foreground;background"  background 15 = white (light mode)
    import os
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parts = colorfgbg.split(";")
        try:
            bg = int(parts[-1])
            return LIGHT if bg == 15 else DARK
        except ValueError:
            pass

    # Check $TERM_PROGRAM or known light-mode terminals
    term = os.environ.get("TERM_PROGRAM", "").lower()
    if term in ("iterm.app",):
        pass  # Could be either; skip

    return DARK  # safe default


# ── Convenience: active theme singleton ──────────────────────────────────────

import os as _os

_active: Theme = resolve_theme(_os.environ.get("SDX_THEME", "auto"))


def set_active_theme(name: str) -> Theme:
    """Switch the active theme at runtime (e.g. from /theme command)."""
    global _active
    _active = resolve_theme(name)
    return _active


def active() -> Theme:
    """Return the currently active theme."""
    return _active