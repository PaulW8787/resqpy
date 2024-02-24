"""TriMeshStencil class for applying convolutions to TriMesh z values."""

import logging

log = logging.getLogger(__name__)

import math as maths
import numpy as np

import resqpy.surface as rqs

root_3 = maths.sqrt(3.0)
root_3_by_2 = root_3 / 2.0


class TriMeshStencil:
    """Class holding a temporary regular hexagonal symmetrical stencil for use with TriMesh objects."""

    def __init__(self, pattern, normalise = None):
        """Initialises a TriMeshStencil object from xml, or from arguments.

        arguments:
            pattern (1D numpy float array): values for one radial arm of the stencil (first value is centre)
            normalise (non zero float, optional): if present, pattern values are normalised to sum to this value,
                then ring values (away from centre) are further decreased by a factor of the number of elements
                in the ring; hence the full stencil values will sum to the normalise value

        returns:
           the newly created TriMeshStencil object
        """

        self.n = None  # length of pattern, including central element
        self.start_ip = None  # 1D numpy int array holding I offsets of start of stencil for each J offset (half hex)
        self.row_length = None  # 1D numpy int array holding number of elements in stencil for each J offset (half hex)
        self.half_hex = None  # 2D numpy float array of stancil values, shifted to left and right padded with NaN

        pattern = np.array(pattern, dtype = float)
        assert pattern.ndim == 1, 'tri mesh stencil pattern must be one dimensional'
        assert len(pattern) > 1, 'tri mesh stancil pattern must contain at least two elements'
        assert not np.any(np.isnan(pattern)), 'tri mesh stencil may not contain NaN'
        if len(pattern) > 50:
            log.warning(f'very large stencil pattern length: {len(pattern)}')

        self.n = len(pattern)

        if normalise is not None:
            assert not maths.isclose(normalise, 0.0), 'calling code must scale to stencil pattern to sum to zero'
            t = np.sum(pattern)
            assert not maths.isclose(t, 0.0), 'pattern sums to zero and cannot be normalised to another value'
            pattern *= normalise / t
            for i in range(1, self.n):
                pattern[i] /= float(6 * i)

        self.pattern = pattern

        # build up half hex stencil as list (over J offsets) of row (ie. for a range of I offsets) values from pattern
        half_hex = []  # list of (ip, length, stencil values) for implicit jp in range 0..n-1
        row_length = 2 * self.n - 1
        half_hex.append((-(self.n - 1), row_length, np.concatenate((np.flip(pattern[1:]), pattern))))
        for jp in range(1, self.n):
            start_ip = -(self.n - 1) + (jp // 2)
            row_length -= 1
            row_v = np.full(row_length, pattern[jp], dtype = float)
            if jp < self.n - 1:
                sub_p = pattern[jp + 1:]
                sub_len = sub_p.size
                row_v[-sub_len:] = sub_p
                row_v[:sub_len] = np.flip(sub_p)
            half_hex.append((start_ip, row_length, row_v))

        # convert into representation more njit & numpy friendly
        self.start_ip = np.array([ip for (ip, _, _) in half_hex], dtype = int)
        self.row_length = np.array([rl for (_, rl, _) in half_hex], dtype = int)
        self.half_hex = np.full((self.n, 2 * self.n - 1), np.NaN, dtype = float)
        for jp in range(self.n):
            self.half_hex[jp, :self.row_length[jp]] = half_hex[jp][2]

    def log(self, log_level = logging.DEBUG):
        """Log stencil."""

        half_lines = []
        for jp in range(self.n):
            padding = self.n + self.start_ip[jp]
            line = ''
            if jp % 2:
                line += '    '
            line += '   x   ' * padding
            for v in self.half_hex[jp, :self.row_length[jp]]:
                line += f' {v:6.3f}'
            line += '   x   ' * padding
            half_lines.append(line)
        for i in range(1, self.n):
            log.log(log_level, half_lines[-i])
            log.log(log_level, '')
        for i in range(self.n):
            log.log(log_level, half_lines[i])
            if i < self.n - 1:
                log.log(log_level, '')

    def apply(self, tri_mesh, handle_nan = True, title = None):
        """Return a new tri mesh with z values generated by applying the stencil to z values of an existing tri mesh.

        arguments:
            tri_mesh (TriMesh): an existing tri mesh to apply the stencil to
            handle_nan (bool, default True): if True, a smoothing style weighted average of non-NaN values is used;
                if False, a simple convolution is applied and will yield NaN where any input within the stencil area
                is NaN
            title (str, optional): the title to use for the new tri mesh; if None, title is inherited from input

        returns:
            a new TriMesh in the same model as the input tri mesh, with the stencil having been applied to z values

        note:

            - this method does not write hdf5 nor create xml for the new tri mesh
        """

        log.info(f'applying stencil to tri mesh: {tri_mesh.title}')

        # create a temporary tri mesh style z array as a copy of the original with a NaN border
        border = self.n
        if border % 2:
            border += 1
        e_nj = tri_mesh.nj + 2 * border
        e_ni = tri_mesh.ni + 2 * border
        z_values = np.full((e_nj, e_ni), np.NaN if handle_nan else 0.0, dtype = float)
        z_values[border:border + tri_mesh.nj, border:border + tri_mesh.ni] = tri_mesh.full_array_ref()[:, :, 2]
        applied = np.full((e_nj, e_ni), np.NaN, dtype = float)

        if handle_nan:
            apply_stencil_nanmean(self.n, self.start_ip, self.row_length, self.half_hex, z_values, applied, border,
                                  tri_mesh.nj, tri_mesh.ni)
        else:
            #TODO: a simple apply function that will result in NaN if any input value within stencil area is NaN
            raise NotImplementedError('only smoothing style tri mesh stencil application currently available')

        # create a new tri mesh object using the values from the applied array for z
        tm = rqs.TriMesh(tri_mesh.model,
                         t_side = tri_mesh.t_side,
                         nj = tri_mesh.nj,
                         ni = tri_mesh.ni,
                         origin = tri_mesh.origin,
                         z_uom = tri_mesh.z_uom,
                         title = title,
                         z_values = applied[border:border + tri_mesh.nj, border:border + tri_mesh.ni],
                         surface_role = tri_mesh.surface_role,
                         crs_uuid = tri_mesh.crs_uuid)

        return tm


# todo: njit with parallel True
def apply_stencil_nanmean(n, start_ip, row_length, half_hex, tm_z, applied, border, onj, oni):
    """Apply the stencil to the tri mesh z values with a weighted nanmean (typically for smoothing)."""

    for j in range(border, border + onj):  # todo: change to numba prange()
        j_odd = j % 2
        for i in range(border, border + oni):
            a = 0.0
            ws = 0.0
            for jp in range(n):
                js_odd = (jp % 2) * j_odd
                i_st = start_ip[jp]
                j_sm = j - jp
                j_sp = j + jp
                for ip in range(row_length[jp]):
                    i_s = i + i_st + ip + js_odd
                    v = tm_z[j_sm, i_s]
                    s = half_hex[jp, ip]
                    if not np.isnan(v):
                        ws += s
                        a += v * s
                    if jp:
                        v = tm_z[j_sp, i_s]
                        if not np.isnan(v):
                            ws += s
                            a += v * s
            if ws != 0.0:
                applied[j, i] = a / ws
