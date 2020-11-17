import math
from pathlib import Path

import implicit
import numpy as np
import pandas as pd
import tensorflow_hub as hub
from scipy.sparse import csr_matrix
from sklearn.metrics.pairwise import euclidean_distances

EMBEDS_USE_URL = "https://tfhub.dev/google/universal-sentence-encoder/4"


def preprocess(path_interactions: str = "interactions.csv",
               path_items: str = "items.csv",
               path_serialised: str = "."):
    """
    Read data, fit the data, comvert to embeddings and serialise.

    Args:
        interactions (str, optional): Interactions file. Defaults to
            "interactions.csv".
        items (str, optional): Items file. Defaults to "items.csv".

    Raises:
        ColumnNotFoundError: If "user", "item", and "interaction"
            columns are not found in the interactions file
        ColumnNotFoundError: If "title" column not found in the
            items file
    """
    # Check file paths
    path_interactions = Path(path_interactions)
    path_items = Path(path_items)
    path_serialised = Path(path_serialised)
    if not path_interactions.exists():
        raise FileNotFoundError("Specify a file for interactions")
    if not path_items.exists():
        raise FileNotFoundError("Specify a file for items")
    if not path_serialised.exists():
        path_serialised.mkdir()

    # Read data
    df_intxn = pd.read_csv(path_interactions)
    df_items = pd.read_csv(path_items, index_col="id")
    if set(df_intxn.columns) - set(["interaction", "item", "user"]):
        raise ColumnNotFoundError(
            "These columns must be present in interactions: "
            f"{str(['interaction', 'item', 'user'])}")
    if "title" not in df_items.columns:
        raise ColumnNotFoundError("`title` must be present in items")

    # Format to usable data
    titles = df_items["title"].tolist()
    batched_titles = batch(titles)
    mat = csr_matrix(
        (df_intxn["interaction"], (df_intxn["item"], df_intxn["user"])))

    # MF Model
    model_mf = implicit.als.AlternatingLeastSquares(factors=8)
    model_mf.fit(mat)

    # USE model
    model_use = hub.load(EMBEDS_USE_URL)

    # MF & USE embeddings
    embeds_mf = model_mf.item_factors.copy()
    embeds_use = [model_use(batched).numpy() for batched in batched_titles]
    embeds_use = np.vstack(embeds_use)

    # Serialise embeddings
    np.save(path_serialised/"embeds_mf.npy", embeds_mf)
    np.save(path_serialised/"embeds_use.npy", embeds_use)


class Recommender:

    def __init__(self,
                 path_items: str = "items.csv",
                 path_interactions: str = "interactions.csv",
                 path_serialised: str = "."):

        path_items = Path(path_items)
        path_interactions = Path(path_interactions)
        path_embeds_mf = Path(path_serialised)/"embeds_mf.npy"
        path_embeds_use = Path(path_serialised)/"embeds_use.npy"

        self.encoder = hub.load(EMBEDS_USE_URL)
        self.embeds_mf = np.load(path_embeds_mf)
        self.embeds_use = np.load(path_embeds_use)
        self.items = pd.read_csv(path_items, index_col="id")
        self.interacted_items = set(pd.read_csv(path_interactions)["item"])

    def recommend(self,
                  query: str,
                  *,
                  K_use: int = 2,
                  K_mf: int = 5,
                  n_to_recommend: int = 5,
                  use_buffer_multiplier: int = 10,
                  mf_buffer_multiplier: int = 10) -> list:
        """Generate a list of recommendations based on a search query

        Args:
            query (str): Search query to provide context.
            K_use (int, optional): To retrieve top items based on semantic
                similarity. Defaults to 2.
            K_mf (int, optional): To retrieve top items based on transactional
                similarity. Defaults to 5.
            n_to_recommend (int, optional): The no. of recommendations that will
                be generated. Defaults to 5.
            use_buffer_multiplier (int, optional): this is needed because we will
                filter out items that have not been interacted. Defaults to 1000.
            mf_buffer_multiplier (int, optional): this is needed because we need to
                ensure the similar mf items don't already exist in the final
                recommendations. Defaults to 10.

        Returns:
            list: item recommendations as item IDs
        """
        # Get encoding
        encoded_query = self.encoder([query])
        encoded_query = encoded_query.numpy()

        # 1. Get nearest USE items
        item_ids = self._find_nearest(
            encoded_query,
            self.embeds_use,
            K=K_use*use_buffer_multiplier)

        # 2. Filter out to items that have not been interacted with
        item_ids = [item_id for item_id in item_ids
                    if item_id in self.interacted_items]

        # 3. Get top `K_use`
        item_ids = item_ids[:K_use]

        # 4. For every item, get `K_mf` neighbours in the MF space
        recs = []
        for item_id in item_ids:
            mf_items = self._find_nearest(
                self.embeds_mf[None, item_id],
                self.embeds_mf,
                K=K_mf*mf_buffer_multiplier)
            rec = np.setdiff1d(mf_items, recs, assume_unique=True)[:K_mf]
            recs.extend(rec)

        # 5. Truncate
        recs = recs[:n_to_recommend]

        # 6. Get titles
        recs_titles = [self.items.loc[idx].item() for idx in recs]

        return recs_titles


    def _find_nearest(self, x, y, K) -> list:
        dists = euclidean_distances(x, y)
        sorted_items = np.argsort(dists)[::-1]
        return sorted_items[0][:K]


def batch(items, batch_size=32):
    num_batches = math.ceil(len(items) / batch_size)
    batched_items = [
        items[batch_size*batch_idx:batch_size*(batch_idx+1)]
        for batch_idx in range(num_batches)]
    return batched_items


class ColumnNotFoundError(Exception):
    pass
