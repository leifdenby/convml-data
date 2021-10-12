from pathlib import Path
import matplotlib.pyplot as plt

import luigi

from ....pipeline import XArrayTarget, YAMLTarget, ImageTarget
from .. import DataSource
from ..sampling import domain as sampling_domain
from ..sampling.interpolation import resample
from . import GenerateSceneIDs
from .sampling import CropSceneSourceFiles, SceneSourceFiles, _SceneRectSampleBase
from ..utils.domain_images import rgb_image_from_scene_data, align_axis_x


def _plot_scene_aux(da_aux, img, **kwargs):
    fig, axes = plt.subplots(nrows=2, figsize=(10.0, 8.0), sharex=True)
    img_extent = [
        da_aux.x.min().item(),
        da_aux.x.max().item(),
        da_aux.y.min().item(),
        da_aux.y.max().item(),
    ]
    axes[0].imshow(img, extent=img_extent)
    axes[1].imshow(img, extent=img_extent)
    if da_aux.name == "HT":
        # cloud-top height [m]
        kwargs["vmin"] = 0.0
        kwargs["vmax"] = 14.0e3
    da_aux.plot(ax=axes[1], y="y", cmap="nipy_spectral", **kwargs)
    align_axis_x(ax=axes[0], ax_target=axes[1])
    return fig, axes


class SceneRegriddedData(_SceneRectSampleBase):
    """
    Regrid the scene source data to a fixed Cartesian resolution
    """

    @property
    def data_source(self):
        return DataSource.load(path=self.data_path)

    def requires(self):
        data_source = self.data_source

        reqs = {}
        if isinstance(data_source.domain, sampling_domain.SourceDataDomain):
            reqs["source_data"] = SceneSourceFiles(
                scene_id=self.scene_id,
                data_path=self.data_path,
                aux_product=self.aux_product,
            )
        else:
            reqs["source_data"] = CropSceneSourceFiles(
                scene_id=self.scene_id,
                data_path=self.data_path,
                pad_ptc=self.crop_pad_ptc,
                aux_product=self.aux_product,
            )

        if self.aux_product is not None:
            reqs["base"] = SceneRegriddedData(
                scene_id=self.scene_id,
                data_path=self.data_path,
                crop_pad_ptc=self.crop_pad_ptc,
            )

        return reqs

    def run(self):
        domain_output = self.output()

        if not domain_output["data"].exists():
            inputs = self.input()
            da_src = inputs["source_data"]["data"].open()

            domain = self.data_source.domain
            if isinstance(domain, sampling_domain.SourceDataDomain):
                domain = domain.generate_from_dataset(ds=da_src)

            data_source = self.data_source
            if (
                "rect" not in data_source.sampling
                or data_source.sampling["rect"].get("dx") is None
            ):
                raise Exception(
                    "To produce isometric grid resampling of the source data please "
                    "define the grid-spacing by setting `dx` in a section called `rect` "
                    "in the `sampling` part of the data source meta information"
                )
            dx = data_source.sampling["rect"]["dx"]

            if self.aux_product is None:
                method = "bilinear"
            else:
                method = "nearest_s2d"
            da_domain = resample(domain=domain, da=da_src, dx=dx, method=method)
            Path(domain_output["data"].fn).parent.mkdir(exist_ok=True, parents=True)
            domain_output["data"].write(da_domain)
        else:
            da_domain = domain_output["data"].open()

        if self.aux_product is not None:
            img_domain = self.input()["base"]["image"].open()
            fig, _ = _plot_scene_aux(da_aux=da_domain, img=img_domain)
            fig.savefig(domain_output["image"].fn)
        else:
            img_domain = rgb_image_from_scene_data(
                data_source=data_source, da_scene=da_domain, src_attrs=da_src.attrs
            )
            img_domain.save(str(domain_output["image"].fn))

    def output(self):
        scene_data_path = Path(self.data_path) / "rect"

        if self.aux_product is not None:
            scene_data_path = scene_data_path / "aux" / self.aux_product

        fn_data = f"{self.scene_id}.nc"
        fn_image = f"{self.scene_id}.png"
        return dict(
            data=XArrayTarget(str(scene_data_path / fn_data)),
            image=ImageTarget(str(scene_data_path / fn_image)),
        )


class GenerateRegriddedScenes(luigi.Task):
    data_path = luigi.Parameter(default=".")
    aux_product = luigi.OptionalParameter(default=None)

    @property
    def data_source(self):
        return DataSource.load(path=self.data_path)

    def requires(self):
        return GenerateSceneIDs(data_path=self.data_path)

    def run(self):
        scene_ids = list(self.input().open().keys())

        tasks_scenes = {}
        for scene_id in scene_ids:
            tasks_scenes[scene_id] = SceneRegriddedData(
                scene_id=scene_id, aux_product=self.aux_product
            )

        yield tasks_scenes

    def output(self):
        fn_output = "regridded_data_by_scene.yaml"
        p = Path(self.data_path) / "rect" / fn_output
        return YAMLTarget(str(p))
