from dataclasses import dataclass, field, asdict
from typing import List, Dict
import h5py
from io import StringIO
import numpy as np
import pandas as pd
from pyiron_base import extract_data_from_file
import warnings


@dataclass
class DumpData:
    steps: List = field(default_factory=lambda: [])
    natoms: List = field(default_factory=lambda: [])
    cells: List = field(default_factory=lambda: [])
    indices: List = field(default_factory=lambda: [])
    forces: List = field(default_factory=lambda: [])
    mean_forces: List = field(default_factory=lambda: [])
    velocities: List = field(default_factory=lambda: [])
    mean_velocities: List = field(default_factory=lambda: [])
    unwrapped_positions: List = field(default_factory=lambda: [])
    mean_unwrapped_positions: List = field(default_factory=lambda: [])
    positions: List = field(default_factory=lambda: [])
    computes: Dict = field(default_factory=lambda: {})


def collect_output_log(file_name, prism):
    """
    general purpose routine to extract static from a lammps log file

    Args:
        file_name:
        prism:

    Returns:

    """
    with open(file_name, "r") as f:
        read_thermo = False
        thermo_lines = ""
        dfs = []
        for l in f:
            l = l.lstrip()

            if read_thermo:
                if l.startswith("Loop"):
                    read_thermo = False
                    continue
                thermo_lines += l

            if l.startswith("Step"):
                read_thermo = True
                thermo_lines += l

        dfs.append(
            pd.read_csv(
                StringIO(thermo_lines),
                sep="\s+",
                engine="c",
            )
        )

    if len(dfs) == 1:
        df = dfs[0]
    else:
        df = pd.concat[dfs]

    h5_dict = {
        "Step": "steps",
        "Temp": "temperature",
        "PotEng": "energy_pot",
        "TotEng": "energy_tot",
        "Volume": "volume",
    }

    for key in df.columns[df.columns.str.startswith("f_mean")]:
        h5_dict[key] = key.replace("f_", "")

    df = df.rename(index=str, columns=h5_dict)
    pressure_dict = dict()
    if all(
        [
            x in df.columns.values
            for x in [
                "Pxx",
                "Pxy",
                "Pxz",
                "Pxy",
                "Pyy",
                "Pyz",
                "Pxz",
                "Pyz",
                "Pzz",
            ]
        ]
    ):
        pressures = (
            np.stack(
                (
                    df.Pxx,
                    df.Pxy,
                    df.Pxz,
                    df.Pxy,
                    df.Pyy,
                    df.Pyz,
                    df.Pxz,
                    df.Pyz,
                    df.Pzz,
                ),
                axis=-1,
            )
            .reshape(-1, 3, 3)
            .astype("float64")
        )
        # Rotate pressures from Lammps frame to pyiron frame if necessary
        if _check_ortho_prism(prism=prism):
            rotation_matrix = prism.R.T
            pressures = rotation_matrix.T @ pressures @ rotation_matrix

        df = df.drop(
            columns=df.columns[
                ((df.columns.str.len() == 3) & df.columns.str.startswith("P"))
            ]
        )
        pressure_dict["pressures"] = pressures
    else:
        warnings.warn(
            "LAMMPS warning: log.lammps does not contain the required pressure values."
        )
    if "mean_pressure[1]" in df.columns:
        pressures = (
            np.stack(
                (
                    df["mean_pressure[1]"],
                    df["mean_pressure[4]"],
                    df["mean_pressure[5]"],
                    df["mean_pressure[4]"],
                    df["mean_pressure[2]"],
                    df["mean_pressure[6]"],
                    df["mean_pressure[5]"],
                    df["mean_pressure[6]"],
                    df["mean_pressure[3]"],
                ),
                axis=-1,
            )
            .reshape(-1, 3, 3)
            .astype("float64")
        )
        if _check_ortho_prism(prism=prism):
            rotation_matrix = prism.R.T
            pressures = rotation_matrix.T @ pressures @ rotation_matrix
        df = df.drop(
            columns=df.columns[
                (
                    df.columns.str.startswith("mean_pressure")
                    & df.columns.str.endswith("]")
                )
            ]
        )
        pressure_dict["mean_pressures"] = pressures
    generic_keys_lst = list(h5_dict.values())
    return generic_keys_lst, pressure_dict, df


def collect_h5md_file(file_name, prism):
    """

    Args:
        file_name:
        cwd:

    Returns:

    """
    if _check_ortho_prism(prism=prism):
        raise RuntimeError(
            "The Lammps output will not be mapped back to pyiron correctly."
        )
    with h5py.File(file_name, mode="r", libver="latest", swmr=True) as h5md:
        positions = [pos_i.tolist() for pos_i in h5md["/particles/all/position/value"]]
        steps = [steps_i.tolist() for steps_i in h5md["/particles/all/position/step"]]
        forces = [for_i.tolist() for for_i in h5md["/particles/all/force/value"]]
        # following the explanation at: http://nongnu.org/h5md/h5md.html
        cell = [
            np.eye(3) * np.array(cell_i.tolist())
            for cell_i in h5md["/particles/all/box/edges/value"]
        ]
        return forces, positions, steps, cell


def collect_errors(file_name):
    """

    Args:
        file_name:

    Returns:

    """
    error = extract_data_from_file(file_name, tag="ERROR", num_args=1000)
    if len(error) > 0:
        error = " ".join(error[0])
        raise RuntimeError("Run time error occurred: " + str(error))
    else:
        return True


def collect_dump_file(file_name, prism, structure, potential_elements):
    """
    general purpose routine to extract static from a lammps dump file

    Args:
        file_name:
        prism:
        structure:
        potential_elements:

    Returns:

    """
    rotation_lammps2orig = prism.R.T
    with open(file_name, "r") as f:
        dump = DumpData()

        for line in f:
            if "ITEM: TIMESTEP" in line:
                dump.steps.append(int(f.readline()))

            elif "ITEM: BOX BOUNDS" in line:
                c1 = np.fromstring(f.readline(), dtype=float, sep=" ")
                c2 = np.fromstring(f.readline(), dtype=float, sep=" ")
                c3 = np.fromstring(f.readline(), dtype=float, sep=" ")
                cell = np.concatenate([c1, c2, c3])
                lammps_cell = to_amat(cell)
                unfolded_cell = prism.unfold_cell(lammps_cell)
                dump.cells.append(unfolded_cell)

            elif "ITEM: NUMBER OF ATOMS" in line:
                n = int(f.readline())
                dump.natoms.append(n)

            elif "ITEM: ATOMS" in line:
                # get column names from line
                columns = line.lstrip("ITEM: ATOMS").split()

                # Read line by line of snapshot into a string buffer
                # Than parse using pandas for speed and column acces
                buf = StringIO()
                for _ in range(n):
                    buf.write(f.readline())
                buf.seek(0)
                df = pd.read_csv(
                    buf,
                    nrows=n,
                    sep="\s+",
                    header=None,
                    names=columns,
                    engine="c",
                )
                df.sort_values(by="id", ignore_index=True, inplace=True)
                # Coordinate transform lammps->pyiron
                dump.indices.append(
                    remap_indices(
                        lammps_indices=df["type"].array.astype(int),
                        potential_elements=potential_elements,
                        structure=structure,
                    )
                )

                force = np.stack(
                    [df["fx"].array, df["fy"].array, df["fz"].array], axis=1
                )
                dump.forces.append(np.matmul(force, rotation_lammps2orig))
                if "f_mean_forces[1]" in columns:
                    force = np.stack(
                        [
                            df["f_mean_forces[1]"].array,
                            df["f_mean_forces[2]"].array,
                            df["f_mean_forces[3]"].array,
                        ],
                        axis=1,
                    )
                    dump.mean_forces.append(np.matmul(force, rotation_lammps2orig))
                if "vx" in columns and "vy" in columns and "vz" in columns:
                    v = np.stack(
                        [
                            df["vx"].array,
                            df["vy"].array,
                            df["vz"].array,
                        ],
                        axis=1,
                    )
                    dump.velocities.append(np.matmul(v, rotation_lammps2orig))

                if "f_mean_velocities[1]" in columns:
                    v = np.stack(
                        [
                            df["f_mean_velocities[1]"].array,
                            df["f_mean_velocities[2]"].array,
                            df["f_mean_velocities[3]"].array,
                        ],
                        axis=1,
                    )
                    dump.mean_velocities.append(np.matmul(v, rotation_lammps2orig))

                if "xsu" in columns:
                    direct_unwrapped_positions = np.stack(
                        [
                            df["xsu"].array,
                            df["ysu"].array,
                            df["zsu"].array,
                        ],
                        axis=1,
                    )
                    dump.unwrapped_positions.append(
                        np.matmul(
                            np.matmul(direct_unwrapped_positions, lammps_cell),
                            rotation_lammps2orig,
                        )
                    )

                    direct_positions = direct_unwrapped_positions - np.floor(
                        direct_unwrapped_positions
                    )
                    dump.positions.append(
                        np.matmul(
                            np.matmul(direct_positions, lammps_cell),
                            rotation_lammps2orig,
                        )
                    )

                if "f_mean_positions[1]" in columns:
                    pos = np.stack(
                        [
                            df["f_mean_positions[1]"].array,
                            df["f_mean_positions[2]"].array,
                            df["f_mean_positions[3]"].array,
                        ],
                        axis=1,
                    )
                    dump.mean_unwrapped_positions.append(
                        np.matmul(
                            np.matmul(pos, lammps_cell),
                            rotation_lammps2orig,
                        )
                    )
                for k in columns:
                    if k.startswith("c_"):
                        kk = k.replace("c_", "")
                        if kk not in dump.computes.keys():
                            dump.computes[kk] = []
                        dump.computes[kk].append(df[k].array)

        return asdict(dump)


def _check_ortho_prism(prism, rtol=0.0, atol=1e-08):
    """
    Check if the rotation matrix of the UnfoldingPrism object is sufficiently close to a unit matrix

    Args:
        prism (pyiron_atomistics.lammps.structure.UnfoldingPrism): UnfoldingPrism object to check
        rtol (float): relative precision for numpy.isclose()
        atol (float): absolute precision for numpy.isclose()

    Returns:
        boolean: True or False
    """
    return np.isclose(prism.R, np.eye(3), rtol=rtol, atol=atol).all()


def to_amat(l_list):
    """

    Args:
        l_list:

    Returns:

    """
    lst = np.reshape(l_list, -1)
    if len(lst) == 9:
        (
            xlo_bound,
            xhi_bound,
            xy,
            ylo_bound,
            yhi_bound,
            xz,
            zlo_bound,
            zhi_bound,
            yz,
        ) = lst

    elif len(lst) == 6:
        xlo_bound, xhi_bound, ylo_bound, yhi_bound, zlo_bound, zhi_bound = lst
        xy, xz, yz = 0.0, 0.0, 0.0
    else:
        raise ValueError("This format for amat not yet implemented: " + str(len(lst)))

    # > xhi_bound - xlo_bound = xhi -xlo  + MAX(0.0, xy, xz, xy + xz) - MIN(0.0, xy, xz, xy + xz)
    # > xhili = xhi -xlo   = xhi_bound - xlo_bound - MAX(0.0, xy, xz, xy + xz) + MIN(0.0, xy, xz, xy + xz)
    xhilo = (
        (xhi_bound - xlo_bound)
        - max([0.0, xy, xz, xy + xz])
        + min([0.0, xy, xz, xy + xz])
    )

    # > yhilo = yhi -ylo = yhi_bound -ylo_bound - MAX(0.0, yz) + MIN(0.0, yz)
    yhilo = (yhi_bound - ylo_bound) - max([0.0, yz]) + min([0.0, yz])

    # > zhi - zlo = zhi_bound- zlo_bound
    zhilo = zhi_bound - zlo_bound

    cell = [[xhilo, 0, 0], [xy, yhilo, 0], [xz, yz, zhilo]]
    return cell


def remap_indices(lammps_indices, potential_elements, structure):
    """
    Give the Lammps-dumped indices, re-maps these back onto the structure's indices to preserve the species.

    The issue is that for an N-element potential, Lammps dumps the chemical index from 1 to N based on the order
    that these species are written in the Lammps input file. But the indices for a given structure are based on the
    order in which chemical species were added to that structure, and run from 0 up to the number of species
    currently in that structure. Therefore we need to be a little careful with mapping.

    Args:
        lammps_indices (numpy.ndarray/list): The Lammps-dumped integers.
        potential_elements (numpy.ndarray/list):
        structure (pyiron_atomistics.atomistics.structure.Atoms):

    Returns:
        numpy.ndarray: Those integers mapped onto the structure.
    """
    lammps_symbol_order = np.array(potential_elements)

    # If new Lammps indices are present for which we have no species, extend the species list
    unique_lammps_indices = np.unique(lammps_indices)
    if len(unique_lammps_indices) > len(np.unique(structure.indices)):
        unique_lammps_indices -= (
            1  # Convert from Lammps start counting at 1 to python start counting at 0
        )
        new_lammps_symbols = lammps_symbol_order[unique_lammps_indices]
        structure.set_species(
            [structure.convert_element(el) for el in new_lammps_symbols]
        )

    # Create a map between the lammps indices and structure indices to preserve species
    structure_symbol_order = np.array([el.Abbreviation for el in structure.species])
    map_ = np.array(
        [
            int(np.argwhere(lammps_symbol_order == symbol)[0]) + 1
            for symbol in structure_symbol_order
        ]
    )

    structure_indices = np.array(lammps_indices)
    for i_struct, i_lammps in enumerate(map_):
        np.place(structure_indices, lammps_indices == i_lammps, i_struct)
    # TODO: Vectorize this for-loop for computational efficiency

    return structure_indices
