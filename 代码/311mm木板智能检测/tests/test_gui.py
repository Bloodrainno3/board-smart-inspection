import pytest
from PySide6.QtWidgets import QApplication
from boardcapture.config import ConfigStore
from boardcapture.gui import Window

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def test_gui_numeric_settings_roundtrip_and_locking(app,tmp_path):
    store=ConfigStore(tmp_path/"settings.json")
    window=Window(store,start_engine=False)
    try:
        window.next_number.setText("100000000000")
        window.select(window.numbering,"descending")
        assert "100000000000.jpg、99999999999.jpg" in window.filename_example.text()
        cfg=window.config_from_fields()
        assert cfg["output"]["next_number"]==100000000000
        assert cfg["output"]["numbering"]=="descending"
        assert cfg["devices"]["front_sensor"]["address"]==13313
        window.update_state(dict(plc=True,camera=True,auto=True,capture=True,saving=0,archives=0,simulation=False,lines=1024))
        assert window.settings_tabs.isEnabled()
        assert window.main_tabs.isEnabled()
        assert not window.port.isEnabled()
        assert not window.trigger_mode.isEnabled()
        window.settings_tabs.setCurrentIndex(1)
        assert window.settings_tabs.currentIndex()==1
        window.main_tabs.setCurrentIndex(1)
        assert window.main_tabs.currentIndex()==1
        assert not window.manual_start.isEnabled()
        assert window.disarm.isEnabled()
        window.on_event("saved",{"path":str(tmp_path/"nonexistent.jpg"),"next_number":99999999999})
        assert window.next_number.text()=="99999999999"
    finally:
        window.close()
        window.engine.encoder.shutdown()
        window.engine.archiver.shutdown()
