"""Shared image coordinates and bounded rendering for pixel-accurate comparison."""
from PIL import Image


class ImageViewport:
    def __init__(self, image_size):
        self.image_size = image_size
        self.view_size = (1, 1)
        self.zoom = 1.0
        self.fit_mode = True
        self.offset = (0, 0)

    @property
    def scaled_size(self):
        return tuple(max(1, round(value * self.zoom)) for value in self.image_size)

    def image_point(self, point):
        scaled = self.scaled_size
        return tuple((point[index] - self.offset[index]) * self.image_size[index] / scaled[index]
                     for index in (0, 1))

    def resize(self, size):
        size = tuple(max(1, int(value)) for value in size)
        if size == self.view_size:
            return
        center = self.image_point(tuple(value / 2 for value in self.view_size))
        self.view_size = size
        if self.fit_mode:
            self.fit()
        else:
            scaled = self.scaled_size
            self.offset = tuple(size[index] / 2 - center[index] * scaled[index] / self.image_size[index]
                                for index in (0, 1))
            self._clamp()

    def fit(self):
        self.fit_mode = True
        self.zoom = min(1.0, *(max(1, self.view_size[index] - 24) / self.image_size[index]
                              for index in (0, 1)))
        self.offset = tuple((self.view_size[index] - self.scaled_size[index]) // 2 for index in (0, 1))
        self._clamp()

    def zoom_to(self, zoom, anchor):
        point = self.image_point(anchor)
        self.fit_mode = False
        self.zoom = min(8.0, max(.01, zoom))
        scaled = self.scaled_size
        self.offset = tuple(anchor[index] - point[index] * scaled[index] / self.image_size[index]
                            for index in (0, 1))
        self._clamp()

    def pan(self, dx, dy):
        previous = self.offset
        self.offset = (previous[0] + dx, previous[1] + dy)
        self._clamp()
        return previous != self.offset

    def _clamp(self):
        scaled = self.scaled_size
        self.offset = tuple((self.view_size[index] - scaled[index]) // 2
                            if scaled[index] <= self.view_size[index] else
                            round(min(0, max(self.view_size[index] - scaled[index], self.offset[index])))
                            for index in (0, 1))

    @property
    def visible_box(self):
        left, top = (max(0, value) for value in self.offset)
        scaled = self.scaled_size
        right, bottom = (min(self.view_size[index], self.offset[index] + scaled[index]) for index in (0, 1))
        return left, top, right - left, bottom - top

    @property
    def source_box(self):
        left, top, width, height = self.visible_box
        scaled = self.scaled_size
        return ((left - self.offset[0]) * self.image_size[0] / scaled[0],
                (top - self.offset[1]) * self.image_size[1] / scaled[1],
                (left + width - self.offset[0]) * self.image_size[0] / scaled[0],
                (top + height - self.offset[1]) * self.image_size[1] / scaled[1])

    def render(self, image):
        if image.size != self.image_size:
            raise ValueError('Вход и результат должны иметь одинаковый размер.')
        _left, _top, width, height = self.visible_box
        if self.zoom == 1.0:
            # Integer placement and an unchanged crop: one source pixel per canvas pixel.
            return image.crop(tuple(int(value) for value in self.source_box))
        return image.resize((width, height), Image.Resampling.LANCZOS, box=self.source_box)
