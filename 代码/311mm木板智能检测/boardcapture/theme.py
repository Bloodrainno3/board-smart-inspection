"""Fixed light palette for Windows light/dark modes and disabled controls."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


def apply_light_theme(app):
    app.setStyle('Fusion')
    if hasattr(app.styleHints(), 'setColorScheme'):
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    colors = {
        'Window':'#f2f5f9', 'WindowText':'#172b43', 'Base':'#ffffff',
        'AlternateBase':'#edf3f9', 'Text':'#172b43', 'Button':'#ffffff',
        'ButtonText':'#172b43', 'BrightText':'#ffffff', 'Highlight':'#1764c0',
        'HighlightedText':'#ffffff', 'ToolTipBase':'#ffffff', 'ToolTipText':'#172b43',
        'PlaceholderText':'#596d82', 'Link':'#1764c0', 'Light':'#ffffff',
        'Midlight':'#edf3f9', 'Mid':'#bcc9d7', 'Dark':'#75869a', 'Shadow':'#45596d',
    }
    for name, color in colors.items():
        role = getattr(QPalette.ColorRole, name)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
            palette.setColor(group, role, QColor(color))
    for name in ('Text','WindowText','ButtonText'):
        palette.setColor(QPalette.ColorGroup.Disabled,getattr(QPalette.ColorRole,name),QColor('#5d6e80'))
    app.setPalette(palette)
