import imgui
import numpy as np
import slideflow as sf
import slideflow.mil
import threading

from os.path import join, exists, dirname
from typing import Dict
from slideflow.mil._params import ModelConfigCLAM, TrainerConfigCLAM
from slideflow.mil.eval import _predict_clam, _predict_mil

from ._utils import Widget
from ..gui import imgui_utils

# -----------------------------------------------------------------------------

def _is_mil_model(path: str) -> bool:
    """Check if a given path is a valid MIL model."""
    return (exists(join(path, 'mil_params.json'))
            or (path.endswith('.pth')
                and dirname(path).endswith('models'))
                and exists(join(dirname(path), '../mil_params.json')))


def _get_mil_params(path: str) -> Dict:
    return sf.util.load_json(join(path, 'mil_params.json'))


def _draw_imgui_info(rows, viz):
    for y, cols in enumerate(rows):
        for x, col in enumerate(cols):
            col = str(col)
            if x != 0:
                imgui.same_line(viz.font_size * (6 + (x - 1) * 6))
            if x == 0:
                imgui.text_colored(col, *viz.theme.dim)
            else:
                with imgui_utils.clipped_with_tooltip(col, 22):
                    imgui.text(imgui_utils.ellipsis_clip(col, 22))

# -----------------------------------------------------------------------------

class MILWidget(Widget):

    tag = 'mil'
    description='Multiple-instance Learning'

    def __init__(self, viz):
        self.viz = viz

        # Encoder, model, and config.
        self.model = None
        self.mil_config = None
        self.encoder = None
        self.mil_params = None
        self.encoder_params = None
        self.normalizer = None
        self.calculate_attention = False

        # Predictions and attention.
        self.predictions = None
        self.attention = None

        # Internals.
        self._show_mil_params = None
        self._rendering_message = "Generating whole-slide prediction..."
        self._generating = False
        self._triggered = False
        self._thread = None
        self._toast = None

    def _reload_wsi(self):
        viz = self.viz
        if viz.wsi:
            viz.tile_px = self.encoder_params['tile_px']
            viz.tile_um = self.encoder_params['tile_um']
            viz.slide_widget.load(viz.wsi.path, mpp=viz.slide_widget.manual_mpp)

    def drag_and_drop_hook(self, path: str) -> bool:
        if _is_mil_model(path):
            self.viz.create_toast('MIL model loaded', icon='success')
            self.encoder, self.normalizer = sf.mil.utils.build_bag_encoder(path)
            self.mil_params = _get_mil_params(path)
            self.encoder_params = self.mil_params['bags_encoder']
            self._reload_wsi()
            self.model, self.mil_config = sf.mil.utils.load_model_weights(path)
            return True
        return False

    def _predict_slide(self):
        viz = self.viz

        self._generating = True
        self._triggered = True

        # Generate features with the loaded encoder.
        masked_bags = self.encoder(viz.wsi, normalizer=self.normalizer)
        bags = np.ma.getdata(masked_bags[~masked_bags.mask.any(axis=2)])
        bags = np.expand_dims(bags, axis=0).astype(np.float32)

        # Generate predictions.
        if (isinstance(self.mil_config, TrainerConfigCLAM)
        or isinstance(self.mil_config.model_config, ModelConfigCLAM)):
            self.predictions, self.attention = _predict_clam(
                self.model,
                bags,
                attention=self.calculate_attention
            )
        else:
            self.predictions, self.attention = _predict_mil(
                self.model,
                bags,
                attention=self.calculate_attention,
                use_lens=self.mil_config.model_config.use_lens
            )

        print("Prediction:", self.predictions)
        print("Attention: ", self.attention)

    def predict_slide(self):
        """Initiate a whole-slide prediction."""
        self.viz.set_message(self._rendering_message)
        self._toast = self.viz.create_toast(
            title="Generating prediction",
            sticky=True,
            spinner=True,
            icon='info'
        )
        self._thread = threading.Thread(target=self._predict_slide)
        self._thread.start()

    def refresh_generating_prediction(self):
        """Refresh render of asynchronous MIL prediction / attention heatmap."""
        if self._thread is not None and not self._thread.is_alive():
            self._generating = False
            self._triggered = False
            self._thread = None
            self.viz.clear_message(self._rendering_message)
            if self._toast is not None:
                self._toast.done()
                self._toast = None
            self.viz.create_toast("Prediction complete.", icon='success')

    def draw_encoder_info(self):
        """Draw a description of the encoder information."""

        viz = self.viz
        if self.encoder_params is None:
            imgui.text("No encoder loaded.")
            return
        c = self.encoder_params

        if 'normalizer' in c and c['normalizer']:
            normalizer = c['normalizer']['method']
        else:
            normalizer = "-"

        rows = [
            ['Encoder',         c['extractor']['class'].split('.')[-1]],
            ['Encoder Args',    c['extractor']['kwargs']],
            ['Normalizer',      normalizer],
            ['Num features',    c['num_features']],
            ['Tile size (px)',  c['tile_px']],
            ['Tile size (um)',  c['tile_um']],
        ]
        _draw_imgui_info(rows, viz)
        imgui_utils.vertical_break()

    def draw_mil_info(self):
        """Draw a description of the MIL model."""

        viz = self.viz
        if self.mil_params is None:
            imgui.text("No MIL model loaded.")
            return
        c = self.mil_params

        rows = [
            ['Outcomes',      c['outcomes']],
            ['Input size',    c['input_shape']],
            ['Output size',   c['output_shape']],
            ['Trainer',       c['trainer']],
        ]
        _draw_imgui_info(rows, viz)

        # MIL model params button and popup.
        with imgui_utils.grayed_out('params' not in c):
            imgui.same_line(imgui.get_content_region_max()[0] - viz.font_size - viz.spacing * 2)
            if imgui.button("HP") and 'params' in c:
                self._show_mil_params = not self._show_mil_params

    def draw_mil_params_popup(self):
        """Draw popup showing MIL model hyperparameters."""

        viz = self.viz
        hp = self.mil_params['params']
        rows = list(zip(list(map(str, hp.keys())), list(map(str, hp.values()))))

        _, self._show_mil_params = imgui.begin("MIL parameters", closable=True, flags=imgui.WINDOW_NO_RESIZE | imgui.WINDOW_NO_SCROLLBAR)
        for y, cols in enumerate(rows):
            for x, col in enumerate(cols):
                if x != 0:
                    imgui.same_line(viz.font_size * 10)
                if x == 0:
                    imgui.text_colored(col, *viz.theme.dim)
                else:
                    imgui.text(col)
        imgui.end()

    @imgui_utils.scoped_by_object_id
    def __call__(self, show=True):
        viz = self.viz

        if self._generating:
            self.refresh_generating_prediction()

        if show:
            viz.header("Multiple-instance Learning")

        if show and self.model:
            if viz.collapsing_header('Encoder', default=True):
                self.draw_encoder_info()
            if viz.collapsing_header('MIL Model', default=True):
                self.draw_mil_info()
                predict_enabled = viz.wsi is not None and self.model is not None
                if viz.sidebar.full_button("Predict Slide", enabled=predict_enabled):
                    self.predict_slide()
        elif show:
            imgui_utils.padded_text('No MIL model has been loaded.', vpad=[int(viz.font_size/2), int(viz.font_size)])

        if self._show_mil_params and self.mil_params:
            self.draw_mil_params_popup()